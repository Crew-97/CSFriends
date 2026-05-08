from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
import traceback
import logging
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from openai import OpenAI
import numpy as np
from pathlib import Path
import chromadb
import pypdf
import io

# ==========================================
# [설정 영역] 사용자 환경에 맞게 수정하세요.
# ==========================================
# OpenAI API 키 (https://platform.openai.com/api-keys)
OPENAI_API_KEY = ""
GPT_MODEL = "gpt-5.4-nano"   # 사용할 GPT 모델명

# 데이터를 읽어올 폴더 경로 (이 폴더 안에 읽을 파일이 반드시 있어야 합니다)
DATA_DIR = Path("./data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


CHUNK_SIZE = 400                # 텍스트를 자를 단위 (글자 수)
OVERLAP = 50                    # 청킹 시 겹치는 문자열 길이
RAG_MODEL_NAME = 'jhgan/ko-sroberta-multitask' # 임베딩 모델 (한국어 특화)
SIMILARITY_THRESHOLD = 0.40    # 검색 결과로 인정할 최소 유사도 점수
CHROMA_DB_PATH = "./chroma_data" # ChromaDB 저장 경로
# ==========================================

# [전역 변수] 서버 실행 중 메모리에 유지될 변수들
DB = []                     # 문서 내용과 출처를 담을 리스트
DB_EMBEDDINGS = None        # Numpy 검색을 위한 벡터 데이터
rag_model = None            # 임베딩 모델 (텍스트 -> 벡터 변환)
openai_client = None        # OpenAI 연결 클라이언트
collection = None           # ChromaDB 컬렉션 객체

# ==========================================
# [헬퍼 함수] 중복 로직을 재사용하기 위한 함수들
# ==========================================
def extract_text(file_path=None, content=None, file_ext=None):
    """파일에서 텍스트를 추출합니다. (PDF 또는 텍스트 파일)"""

    def read_pdf(reader: pypdf.PdfReader) -> str:
        # ✅ extract_text()가 None을 반환하는 PDF가 종종 있음 -> 안전 처리
        texts = []
        for p in reader.pages:
            texts.append(p.extract_text() or "")
        return "\n".join(texts)

    if file_path:
        ext = file_path.suffix.lower()
        if ext == ".pdf":
            return read_pdf(pypdf.PdfReader(file_path))
        return file_path.read_text(encoding="utf-8", errors="ignore")

    if content is not None and file_ext:
        if file_ext == ".pdf":
            return read_pdf(pypdf.PdfReader(io.BytesIO(content)))
        return content.decode("utf-8", errors="ignore")

    return ""

def chunk_text(text, size=CHUNK_SIZE, overlap=OVERLAP):
    """텍스트를 지정된 크기로 분할하며 겹치는 부분을 포함합니다."""
    if overlap >= size:
        raise ValueError("Overlap must be less than chunk size")
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return chunks

def normalize(vecs):
    """
    L2 정규화 (1D/2D 모두 지원)
    - vecs: (d,) 또는 (n, d)
    """
    arr = np.asarray(vecs, dtype=np.float32)

    # 1D 벡터면 (1, d)로 바꿔서 처리
    if arr.ndim == 1:
        denom = np.linalg.norm(arr) + 1e-12
        return arr / denom

    # 2D 배치면 행 단위로 정규화
    denom = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
    return arr / denom


def add_to_db(chunks, source):
    """청크들을 DB와 ChromaDB에 추가합니다."""
    global DB, DB_EMBEDDINGS
    start_id = len(DB)
    new_items = [{"id": str(start_id + i), "text": c, "source": source} for i, c in enumerate(chunks)]
    DB.extend(new_items)

    # 검색 기능을 위한 임베딩 백터 생성
    new_embeddings = rag_model.encode([item["text"] for item in new_items])
    new_embeddings = normalize(new_embeddings)

    if DB_EMBEDDINGS is None or len(DB_EMBEDDINGS) == 0:
        DB_EMBEDDINGS = new_embeddings
    else:
        DB_EMBEDDINGS = np.vstack([DB_EMBEDDINGS, new_embeddings])

    # chromaDB에 임베딩 벡터 저장
    collection.add(
        documents=[item["text"] for item in new_items],
        embeddings=new_embeddings.tolist(),
        metadatas=[{"source": item["source"]} for item in new_items],
        ids=[item["id"] for item in new_items]
    )
    return len(new_items)


# ==========================================
# [서버 수명주기] 시작 시 1회 실행되는 초기화 로직
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global DB, DB_EMBEDDINGS, rag_model, openai_client, collection

    # 1. 각종 클라이언트 및 모델 로딩
    print("1. AI 모델 및 클라이언트 초기화 중...")
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    rag_model = SentenceTransformer(RAG_MODEL_NAME)
    
    # ChromaDB 연결 (기존 데이터 삭제 후 새로 생성 - 항상 최신 상태 유지)
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    try: 
        chroma_client.delete_collection("ssafy_docs") # 기존 데이터 삭제
    except: 
        pass 
    collection = chroma_client.create_collection("ssafy_docs", metadata={"hnsw:space": "cosine"})

# 2. 데이터 로딩 (PDF 포함 모든 파일)
    print(f"2. 파일 읽는 중... [{DATA_DIR}]")
    for f in DATA_DIR.glob("*"):
        if not f.is_file(): continue
        try:
            text = extract_text(file_path=f)
            if not text.strip(): continue
            chunks = chunk_text(text)
            add_to_db(chunks, f.name)
            print(f"   - [성공] {f.name}")
        except Exception as e:
            print(f"[로드 실패] {f.name}: {e}")

    print(f"3. 벡터 생성 완료! (총 {len(DB)}개 청크)")
    
    print("준비 완료! 서버가 시작되었습니다.")
    yield # 서버 가동
    print("서버가 종료됩니다.")

tags_metadata = [
    {"name": "Chat", "description": "GPT 채팅 관련 API"},
    {"name": "Search", "description": "벡터 검색 API"},
    {"name": "RAG", "description": "검색 + 생성 통합 API"},
    {"name": "Upload", "description": "문서 업로드 API"},
]

app = FastAPI(
    title="SSAFY RAG Chatbot API",
    description="RAG 기반 챗봇 서버 (FastAPI + ChromaDB + OpenAI)",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    debug=True,
)

# CORS 설정 (모든 요청 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# 요청 데이터 구조 정의
class ChatReq(BaseModel):
    message: str

    model_config = {
        "json_schema_extra": {
            "examples": [{"message": "SSAFY 수료 기준에 대해서 알려줘"}]
        }
    }


# ==========================================
# [API 1] 일반 채팅 (GPT 그대로 사용)
# ==========================================
@app.post("/chat", tags=["Chat"], summary="일반 채팅", description="GPT 모델에 메시지를 그대로 전달해 답변을 받습니다.")
def chat(req: ChatReq):
    res = openai_client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": req.message}]
    )
    return {"answer": res.choices[0].message.content}


# ==========================================
# [API 2] 단순 검색 (Numpy cosine similarity)
# - debug/top_k 옵션은 교육용 디버깅에 매우 유용(검색 후보를 눈으로 확인 가능)
# ==========================================
@app.post("/search", tags=["Search"], summary="벡터 유사도 검색", description="질문을 임베딩한 뒤 코사인 유사도로 가장 비슷한 청크를 반환합니다.")
def search(
    req: ChatReq
):
    if DB_EMBEDDINGS is None or len(DB) == 0:
        return {"answer": "DB가 비어있습니다. data 폴더에 문서를 넣거나 upload 하세요.", "score": None, "source": None}

    query_vec = normalize(rag_model.encode(req.message))  # (d,)
    scores = np.dot(DB_EMBEDDINGS, query_vec)             # cosine similarity처럼 해석 가능

    best_idx = int(scores.argmax())
    best_score = float(scores[best_idx])

    if best_score < SIMILARITY_THRESHOLD:
        return {
            "answer": "관련된 내용을 찾을 수 없습니다.",
            "score": best_score,
            "source": None
        }

    return {
        "answer": DB[best_idx]["text"],
        "score": best_score,
        "source": DB[best_idx]["source"]
        }


# ==========================================
# [API 3] 통합 채팅 (RAG: 검색 + 생성)
# - n_results=1 → 3으로 상향 (한 청크가 빗나가도 안전)
# - threshold 통과한 청크만 컨텍스트에 포함
# ==========================================
@app.post("/integrated-chat", tags=["RAG"], summary="RAG 통합 채팅", description="ChromaDB 검색 결과를 컨텍스트로 GPT에 전달해 답변을 생성합니다.")
def integrated_chat(
    req: ChatReq,
    n_results: int = Query(3, ge=1, le=8),
    debug: bool = Query(False)
):
    q = normalize(rag_model.encode([req.message]))  # (1, d) 정규화
    results = collection.query(query_embeddings=q.tolist(), n_results=n_results)

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    # Chroma는 distance(작을수록 유사)를 주므로 similarity로 변환해서 사용
    candidates = []
    for doc, meta, dist in zip(docs, metas, dists):
        sim = 1.0 - float(dist)
        candidates.append({
            "source": meta.get("source"),
            "similarity": sim,
            "text": doc
        })

    # ✅ (개선) threshold 통과한 것만 컨텍스트로 사용
    picked = [c for c in candidates if c["similarity"] >= SIMILARITY_THRESHOLD]

    if picked:
        # 여러 청크를 하나의 컨텍스트로 합침 (너무 길어지면 top 몇 개만)
        picked = sorted(picked, key=lambda x: x["similarity"], reverse=True)[:n_results]
        context = "\n\n".join(
            [f"[출처: {p['source']} | 유사도: {p['similarity']:.2f}]\n{p['text']}" for p in picked]
        )
        # 출처 태그는 여러 개일 수 있으니 요약해서 표시
        source_tag = ", ".join(sorted({p["source"] for p in picked}))
    else:
        context = "(검색결과 없음)"
        source_tag = "ChatGPT 일반 지식"

    prompt = f"""
당신은 SSAFY 교육 과정을 돕는 AI 어시스턴트입니다.
아래의 [참고 문서] 내용을 바탕으로 사용자의 질문에 답변하세요.
문서에 없는 내용은 지어내지 말고, 일반 지식으로 답변하되
문서에 없다는 점을 언급해주세요.

[참고문서]
{context}
""".strip()

    res = openai_client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": req.message}
        ]
    )

    payload = {"answer": res.choices[0].message.content, "source": source_tag}
    if debug:
        payload["candidates"] = [{"source": c["source"], "similarity": round(c["similarity"], 4)} for c in candidates]
        payload["picked"] = [{"source": p["source"], "similarity": round(p["similarity"], 4)} for p in picked]
    return payload

# ==========================================
# [API 4] 파일 업로드 (동적 문서 추가)
# ==========================================
@app.post("/upload", tags=["Upload"], summary="문서 업로드", description="txt / md / pdf 파일을 업로드해 DB에 추가합니다.")
async def upload_file(request: Request, file: UploadFile = File(...)):
    # 허용된 파일 확장자 검사
    allowed_extensions = {".txt", ".md", ".pdf"}
    file_ext = Path(file.filename).suffix.lower()

    if file_ext not in allowed_extensions:
        return {
            "success": False,
            "message": f"지원하지 않는 파일 형식입니다. ({', '.join(allowed_extensions)} 만 허용)"
        }

    try:
        content = await file.read()
        text = extract_text(content=content, file_ext=file_ext)

        if not text.strip():
            return {"success": False, "message": "파일에 내용이 없습니다."}

        chunks = chunk_text(text)
        chunks_added = add_to_db(chunks, file.filename)

        # 파일을 data 폴더에도 저장 (서버 재시작 시에도 유지)
        save_path = DATA_DIR / file.filename
        with open(save_path, "wb") as f:
            f.write(content)

        # Referer 헤더가 있으면 원래 페이지로 리다이렉트
        referer = request.headers.get("referer")
        if referer:
            return RedirectResponse(url=referer, status_code=303)

        return {
            "success": True,
            "message": f"'{file.filename}' 업로드 완료! ({chunks_added}개 청크 추가)",
            "chunks_added": chunks_added
        }

    except Exception as e:
        return {"success": False, "message": f"파일 처리 중 오류 발생: {str(e)}"}


if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0은 외부 접속을 허용한다는 의미입니다.
    # reload=True: 코드 수정 시 서버 자동 재시작 (디버그/개발 모드)
    # log_level="debug": 상세 로그 출력
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="debug",
    )