# CSFriends RAG Chatbot
<img width="1920" height="1683" alt="image" src="https://github.com/user-attachments/assets/47e43962-6c1c-4ef2-82ba-e00e08bd7fc3" />

CSFriends는 CS 면접 및 기술 문서 데이터를 기반으로 답변하는 RAG 챗봇 프로젝트입니다. 기존 Frontend 디자인은 유지하고, FastAPI Backend에서 문서 재귀 탐색, 청킹, 임베딩, ChromaDB 검색, OpenAI 응답 생성을 담당합니다.

## 기술 스택

- Frontend: HTML, CSS, JavaScript
- Backend: FastAPI, Uvicorn
- LLM: OpenAI Chat Completions API
- Embedding: SentenceTransformer `jhgan/ko-sroberta-multitask`
- Vector DB: ChromaDB persistent mode
- Document parsing: Markdown, TXT, PDF(`pypdf`)
- Environment: `python-dotenv`

## 프로젝트 구조

```text
PJT_01_Chatbot/
├─ .env.example
├─ .gitignore
├─ README.md
├─ REPORT.md
├─ docs/
│  ├─ screenshots/
│  ├─ architecture/
│  └─ sample-questions/
├─ index.html
├─ script.js
├─ style.css
└─ servers/
   ├─ main.py
   ├─ requirements.txt
   ├─ data/
   └─ chroma_data/
```

## 설치 및 실행

```bash
cd servers
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:OPENAI_API_KEY="sk-..."
python main.py
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
python main.py
```

## .env 사용 방법

프로젝트 루트에 `.env` 파일을 생성합니다.

```text
OPENAI_API_KEY=sk-...
```

또는 예시 파일을 복사해서 사용할 수 있습니다.

```powershell
Copy-Item .env.example .env
```

`.env.example`에는 실제 키를 넣지 않습니다.

```text
OPENAI_API_KEY=your_openai_api_key_here
```

보안상 `.env` 파일은 GitHub에 업로드하면 안 됩니다. 이 프로젝트의 `.gitignore`에는 `.env`가 포함되어 있어 실제 API 키가 커밋되지 않도록 구성되어 있습니다.

## 서버 접속

서버 실행 후 API 문서는 아래 주소에서 확인합니다.

```text
http://localhost:8000/docs
```

Frontend는 프로젝트 루트의 `index.html`을 브라우저에서 열어 사용할 수 있습니다.

## RAG 동작 방식

1. 서버 시작 시 `servers/data`를 `os.walk()`로 재귀 탐색합니다.
2. `.md`, `.txt`, `.pdf` 파일만 읽고 빈 파일과 깨진 PDF는 skip합니다.
3. Markdown은 `#`, `##`, `###` 제목 기준으로 section을 분리합니다.
4. 긴 section은 `CHUNK_SIZE`, `CHUNK_OVERLAP` 기준으로 추가 청킹합니다.
5. 각 chunk에 `source`, `category`, `section`, `chunk_index` metadata를 저장합니다.
6. SentenceTransformer로 임베딩한 뒤 ChromaDB persistent collection에 저장합니다.
7. `/integrated-chat` 요청 시 벡터 검색 결과를 context로 OpenAI 모델에 전달합니다.

## data 폴더

`servers/data`는 CS 면접/기술 문서의 원천 데이터 폴더입니다. 하위 폴더를 자유롭게 구성할 수 있으며 서버는 모든 하위 폴더를 재귀 탐색합니다.

예시 metadata:

```json
{
  "source": "02-backend-engineering/database/indexing.md",
  "category": "02-backend-engineering",
  "section": "B-Tree Index"
}
```

## chroma_data 폴더

`servers/chroma_data`는 ChromaDB 영속 저장소입니다. 폴더가 없으면 서버 시작 시 자동 생성됩니다. 현재 구현은 서버 시작 시 컬렉션을 재생성해 `data` 폴더의 최신 상태와 검색 DB를 일치시킵니다.

## 주요 API

- `POST /chat`: 일반 OpenAI 챗봇 응답
- `POST /search?top_k=5`: 벡터 검색 결과와 similarity score 반환
- `POST /integrated-chat?top_k=5&debug=false`: RAG 기반 메인 챗봇 응답
- `POST /upload`: `.md`, `.txt`, `.pdf` 문서 업로드 및 즉시 인덱싱

`/integrated-chat` 응답 예시:

```json
{
  "answer": "참고 문서 기반 답변입니다. ...",
  "used_rag": true,
  "sources": ["02-backend-engineering/database/indexing.md"]
}
```

## 예시 질문

- 프로세스와 스레드의 차이를 면접 답변처럼 설명해줘.
- TCP 3-way handshake 과정을 설명해줘.
- B-Tree 인덱스가 데이터베이스 조회 성능을 높이는 이유는?
- 싱글톤 패턴의 장점과 단점은?
- JWT 인증 방식의 장점과 주의할 점은?

## 실행 화면 캡처 위치

제출용 실행 화면은 `docs/screenshots/` 폴더에 저장합니다. 예: `docs/screenshots/chat-result.png`, `docs/screenshots/search-api.png`
