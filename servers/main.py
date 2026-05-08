import hashlib
import io
import logging
import os
import re
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import chromadb
import pypdf
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from openai import OpenAI
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("cs-rag-chatbot")

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
CHROMA_DB_PATH = BASE_DIR / "chroma_data"

ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf"}
COLLECTION_NAME = "cs_interview_docs"
EMBEDDING_MODEL_NAME = "jhgan/ko-sroberta-multitask"
GPT_MODEL = "gpt-5.4-nano"

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
SIMILARITY_THRESHOLD = 0.60
MAX_CONTEXT_CHARS = 6000

rag_model: SentenceTransformer | None = None
openai_client: OpenAI | None = None
collection: Any = None


class ChatReq(BaseModel):
    message: str = Field(..., min_length=1, examples=["B-Tree 인덱스가 무엇인지 설명해줘"])


def get_openai_client() -> OpenAI:
    if openai_client is None:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY 환경변수가 설정되지 않았습니다. API 키를 설정한 뒤 다시 요청하세요.",
        )
    return openai_client


def get_rag_model() -> SentenceTransformer:
    if rag_model is None:
        raise HTTPException(status_code=503, detail="임베딩 모델이 아직 준비되지 않았습니다.")
    return rag_model


def get_collection() -> Any:
    if collection is None:
        raise HTTPException(status_code=503, detail="벡터 DB가 아직 준비되지 않았습니다.")
    return collection


def extract_text_from_pdf_bytes(content: bytes, source: str) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.warning("PDF 텍스트 추출 실패, skip: %s (%s)", source, exc)
        return ""


def extract_text(file_path: Path | None = None, content: bytes | None = None, file_ext: str | None = None) -> str:
    try:
        if file_path is not None:
            ext = file_path.suffix.lower()
            if ext == ".pdf":
                return extract_text_from_pdf_bytes(file_path.read_bytes(), str(file_path))
            return file_path.read_text(encoding="utf-8", errors="ignore")

        if content is not None and file_ext is not None:
            if file_ext == ".pdf":
                return extract_text_from_pdf_bytes(content, "uploaded-pdf")
            return content.decode("utf-8", errors="ignore")
    except UnicodeError as exc:
        logger.warning("인코딩 오류로 파일을 건너뜁니다: %s", exc)
    except Exception as exc:
        logger.warning("파일 읽기 실패: %s", exc)
    return ""


def relative_source(file_path: Path) -> str:
    return file_path.relative_to(DATA_DIR).as_posix()


def category_from_source(source: str) -> str:
    parts = Path(source).parts
    return parts[0] if parts else "uncategorized"


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_markdown_sections(text: str, default_section: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^(#{1,3})\s+(.+?)\s*$", text))
    if not matches:
        return [(default_section, normalize_whitespace(text))]

    sections: list[tuple[str, str]] = []
    preface = normalize_whitespace(text[: matches[0].start()])
    if preface:
        sections.append(("문서 개요", preface))

    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        title = match.group(2).strip()
        body = normalize_whitespace(text[start:end])
        if body:
            sections.append((title, body))
    return sections


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = normalize_whitespace(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    if overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def build_chunks_for_document(text: str, source: str, file_ext: str) -> list[dict[str, Any]]:
    category = category_from_source(source)
    default_section = Path(source).stem
    sections = (
        split_markdown_sections(text, default_section)
        if file_ext == ".md"
        else [("전체 문서", normalize_whitespace(text))]
    )

    items: list[dict[str, Any]] = []
    for section_title, section_text in sections:
        for chunk_index, chunk in enumerate(chunk_text(section_text)):
            items.append(
                {
                    "text": chunk,
                    "metadata": {
                        "source": source,
                        "category": category,
                        "section": section_title,
                        "chunk_index": chunk_index,
                    },
                }
            )
    return items


def make_chunk_id(source: str, section: str, chunk_index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source}:{section}:{chunk_index}:{text}".encode("utf-8")).hexdigest()
    return digest


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_rag_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


def add_chunks_to_collection(chunks: list[dict[str, Any]]) -> int:
    if not chunks:
        return 0

    docs = [chunk["text"] for chunk in chunks]
    metas = [chunk["metadata"] for chunk in chunks]
    ids = [
        make_chunk_id(meta["source"], meta["section"], int(meta["chunk_index"]), doc)
        for doc, meta in zip(docs, metas)
    ]
    embeddings = embed_texts(docs)

    # 중복 처리 정책:
    # 서버 시작 시에는 컬렉션을 재생성해 현재 data 폴더 상태와 DB를 일치시킨다.
    # 업로드 또는 런타임 추가 시에는 upsert를 사용해 동일 id 문서는 갱신하고 신규 문서는 추가한다.
    get_collection().upsert(documents=docs, embeddings=embeddings, metadatas=metas, ids=ids)
    return len(chunks)


def iter_data_files() -> list[Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for root, _, filenames in os.walk(DATA_DIR):
        for filename in filenames:
            path = Path(root) / filename
            if path.suffix.lower() in ALLOWED_EXTENSIONS:
                files.append(path)
    return sorted(files)


def load_data_documents() -> int:
    total_chunks = 0
    files = iter_data_files()
    if not files:
        logger.warning("data 폴더에 인덱싱할 문서가 없습니다: %s", DATA_DIR)
        return 0

    logger.info("문서 인덱싱 시작: %s개 파일", len(files))
    for file_path in files:
        source = relative_source(file_path)
        try:
            text = extract_text(file_path=file_path)
            if not text.strip():
                logger.info("빈 파일 skip: %s", source)
                continue
            chunks = build_chunks_for_document(text, source, file_path.suffix.lower())
            added = add_chunks_to_collection(chunks)
            total_chunks += added
            logger.info("인덱싱 완료: %s (%s chunks)", source, added)
        except Exception as exc:
            logger.warning("문서 인덱싱 실패: %s (%s)", source, exc)
    logger.info("문서 인덱싱 종료: 총 %s chunks", total_chunks)
    return total_chunks


def chroma_distance_to_similarity(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 - float(distance)))


def vector_search(query: str, top_k: int) -> list[dict[str, Any]]:
    count = get_collection().count()
    if count == 0:
        return []

    query_embedding = embed_texts([query])[0]
    results = get_collection().query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    search_results: list[dict[str, Any]] = []
    for doc, meta, distance in zip(docs, metas, distances):
        search_results.append(
            {
                "text": doc,
                "source": meta.get("source", "unknown"),
                "category": meta.get("category", "uncategorized"),
                "section": meta.get("section", ""),
                "similarity": round(chroma_distance_to_similarity(distance), 4),
            }
        )
    return search_results


def build_rag_prompt(context: str, used_rag: bool) -> str:
    return f"""
당신은 CS 기술 면접을 돕는 한국어 RAG 챗봇입니다.
답변은 반드시 한국어로 작성합니다.
검색 문서가 질문과 직접 관련된 경우에만 참고 문서 기반으로 답변합니다.
참고 문서가 질문과 직접 관련 없거나 근거가 부족하면 문서를 억지로 사용하지 않습니다.
used_rag=False인 경우 일반 지식 기반 답변을 허용합니다.
모르는 내용은 꾸며내지 말고 모른다고 답변합니다.
문서를 그대로 복붙하지 말고 자연스럽게 설명합니다.
마크다운 문법 사용을 최소화하고, 굵은 글씨는 사용하지 않습니다.
과도한 bullet list 대신 사람이 말하듯 자연스러운 문장으로 답변합니다.
핵심 개념을 먼저 쉽게 설명하고, 필요할 때만 짧은 예시를 추가합니다.
설명은 CS 기술 면접에서 바로 말할 수 있는 수준으로 간결하게 정리합니다.
답변 앞에 참고 문서 기반 여부를 알리는 고정 문구를 붙이지 않습니다.

[참고 문서]
{context}
""".strip()


def answer_policy_prefix(used_rag: bool) -> str:
    return (
        "참고 문서 기반 답변입니다."
        if used_rag
        else "참고 문서에서 충분한 근거를 찾지 못해 일반 지식 기반으로 답변합니다."
    )


def ensure_policy_prefix(answer: str, used_rag: bool) -> str:
    return (answer or "").strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_model, openai_client, collection

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DB_PATH.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        openai_client = OpenAI(api_key=api_key)
        logger.info("OpenAI client initialized from OPENAI_API_KEY")
    else:
        openai_client = None
        logger.warning("OPENAI_API_KEY가 없습니다. /chat, /integrated-chat 호출 시 명확한 오류를 반환합니다.")

    logger.info("임베딩 모델 로딩: %s", EMBEDDING_MODEL_NAME)
    rag_model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = chroma_client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    load_data_documents()
    logger.info("서버 준비 완료")

    yield

    logger.info("서버 종료")


tags_metadata = [
    {"name": "Chat", "description": "일반 OpenAI 챗봇 API"},
    {"name": "Search", "description": "SentenceTransformer + ChromaDB 벡터 검색 API"},
    {"name": "RAG", "description": "검색 문서 기반 통합 챗봇 API"},
    {"name": "Upload", "description": "문서 업로드 및 동적 인덱싱 API"},
]

app = FastAPI(
    title="CS RAG Chatbot API",
    description="FastAPI, OpenAI, SentenceTransformer, ChromaDB 기반 CS 면접 RAG 챗봇",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error: %s\n%s", exc, traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.get("/", tags=["Chat"])
def health_check():
    return {
        "status": "ok",
        "indexed_chunks": get_collection().count() if collection is not None else 0,
        "data_dir": str(DATA_DIR),
    }


@app.post("/chat", tags=["Chat"], summary="일반 OpenAI 챗봇")
def chat(req: ChatReq):
    try:
        res = get_openai_client().chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "당신은 한국어로 답변하는 CS 학습 도우미입니다."},
                {"role": "user", "content": req.message},
            ],
        )
        return {"answer": res.choices[0].message.content}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI API 호출 실패: {exc}") from exc


@app.post("/search", tags=["Search"], summary="벡터 검색")
def search(req: ChatReq, top_k: int = Query(5, ge=1, le=20)):
    results = vector_search(req.message, top_k)
    return {"results": results}


@app.post("/integrated-chat", tags=["RAG"], summary="RAG 통합 챗봇")
def integrated_chat(
    req: ChatReq,
    top_k: int = Query(5, ge=1, le=20),
    debug: bool = Query(False),
):
    candidates = vector_search(req.message, top_k)
    picked = [candidate for candidate in candidates if candidate["similarity"] >= SIMILARITY_THRESHOLD]
    used_rag = len(picked) > 0

    if used_rag:
        context_parts = []
        current_length = 0
        for item in picked:
            block = (
                f"[source: {item['source']} | section: {item['section']} | "
                f"similarity: {item['similarity']}]\n{item['text']}"
            )
            if current_length + len(block) > MAX_CONTEXT_CHARS:
                break
            context_parts.append(block)
            current_length += len(block)
        context = "\n\n".join(context_parts)
    else:
        context = "검색된 참고 문서가 없거나 유사도가 기준값보다 낮습니다."

    try:
        res = get_openai_client().chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": build_rag_prompt(context, used_rag)},
                {"role": "user", "content": req.message},
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI API 호출 실패: {exc}") from exc

    sources = sorted({item["source"] for item in picked})
    payload: dict[str, Any] = {
        "answer": ensure_policy_prefix(res.choices[0].message.content, used_rag),
        "used_rag": used_rag,
        "sources": sources,
    }
    if debug:
        payload["debug"] = {
            "candidates": candidates,
            "selected_chunks": picked,
            "similarity_threshold": SIMILARITY_THRESHOLD,
        }
    return payload


@app.post("/upload", tags=["Upload"], summary="문서 업로드")
async def upload_file(request: Request, file: UploadFile = File(...)):
    file_ext = Path(file.filename or "").suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        return {
            "success": False,
            "message": f"지원하지 않는 파일 형식입니다. 허용 형식: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        }

    try:
        content = await file.read()
        text = extract_text(content=content, file_ext=file_ext)
        if not text.strip():
            return {"success": False, "message": "파일에서 읽을 수 있는 텍스트가 없습니다."}

        safe_name = Path(file.filename or "uploaded").name
        save_path = DATA_DIR / safe_name
        save_path.write_bytes(content)

        chunks = build_chunks_for_document(text, safe_name, file_ext)
        chunks_added = add_chunks_to_collection(chunks)

        referer = request.headers.get("referer")
        if referer:
            return RedirectResponse(url=referer, status_code=303)

        return {
            "success": True,
            "message": f"'{safe_name}' 업로드 및 인덱싱 완료",
            "chunks_added": chunks_added,
        }
    except Exception as exc:
        logger.error("업로드 처리 실패: %s\n%s", exc, traceback.format_exc())
        return {"success": False, "message": f"파일 처리 중 오류 발생: {exc}"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
