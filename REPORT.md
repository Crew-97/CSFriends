# CSFriends RAG Chatbot Report

## 생성형 AI API 호출 흐름

사용자가 Frontend에서 질문을 입력하면 `script.js`가 FastAPI의 `/integrated-chat` API로 메시지를 전송한다. 서버는 질문을 SentenceTransformer로 임베딩하고 ChromaDB에서 관련 chunk를 검색한다. 검색 결과 중 similarity가 기준값 이상인 chunk만 context로 사용하며, 근거가 부족하면 일반 LLM 답변으로 전환한다. 최종 응답은 `answer`, `used_rag`, `sources`를 포함한 JSON 형식으로 반환된다.

## RAG 구조 설명

이 프로젝트의 RAG는 `문서 로딩 -> 청킹 -> 임베딩 -> 벡터 저장 -> 검색 -> 생성` 순서로 동작한다. 서버 시작 시 `servers/data` 폴더를 `os.walk()`로 재귀 탐색하고, `.md`, `.txt`, `.pdf` 파일을 자동 인덱싱한다. 사용자의 질문이 들어오면 벡터 검색으로 관련 문서를 찾고, 충분히 유사한 문서가 있을 때만 답변 근거로 사용한다.

## 청킹 방식 설명

Markdown 문서는 `#`, `##`, `###` 제목을 기준으로 section을 분리한다. 각 chunk metadata에는 파일 상대 경로인 `source`, 최상위 카테고리 폴더명인 `category`, Markdown section 제목인 `section`, 그리고 `chunk_index`를 저장한다. 긴 section은 `CHUNK_SIZE=900`, `CHUNK_OVERLAP=120` 기준으로 추가 분할한다. 제목이 없는 문서나 TXT/PDF는 전체 문서 기준 fallback chunking을 수행한다.

## 벡터 검색 방식 설명

질문과 문서 chunk는 SentenceTransformer `jhgan/ko-sroberta-multitask` 모델로 임베딩한다. ChromaDB는 cosine distance 기반 collection으로 구성되며, API에서는 `1 - distance` 값을 similarity score로 반환한다. 현재 `/integrated-chat`은 similarity가 `SIMILARITY_THRESHOLD=0.60` 이상인 chunk만 RAG context로 사용한다. 이 기준을 통해 질문과 애매하게만 관련된 문서를 억지로 참고하는 문제를 줄였다.

## RAG 사용 조건과 fallback 정책

검색 결과가 존재하더라도 threshold 이상인 chunk가 없으면 `used_rag=false`로 처리한다. 이 경우 답변은 참고 문서를 억지로 사용하지 않고 일반 지식 기반으로 생성된다. 반대로 기준을 넘는 chunk가 있으면 `used_rag=true`로 처리하고, 해당 문서를 우선 근거로 사용한다. `sources`는 JSON에 유지되어 어떤 문서가 참고되었는지 확인할 수 있다.

## 답변 스타일 개선

초기 구현에서는 답변 앞에 “참고 문서 기반 답변입니다.” 같은 고정 문구를 강제로 붙였다. 현재는 이 prefix를 제거하고, 답변이 자연스럽게 시작되도록 수정했다. 또한 시스템 프롬프트에 문서를 그대로 복붙하지 말 것, 마크다운 문법을 최소화할 것, 굵은 글씨를 사용하지 말 것, 사람이 말하듯 자연스럽게 설명할 것을 반영했다. 결과적으로 문서 요약문 같은 답변보다 실제 챗봇에 가까운 말투로 응답한다.

## 문서 업로드 전/후 응답 차이

문서 업로드 전에는 `servers/data`에 포함된 기본 CS 문서만 검색 대상이다. 관련 문서가 부족하면 서버는 일반 지식 기반 답변으로 fallback한다. 문서를 업로드한 후에는 업로드 파일이 즉시 ChromaDB에 upsert되어 검색 대상에 포함된다. 업로드 문서가 질문과 충분히 유사하면 RAG 기반 답변에 사용되고, 그렇지 않으면 일반 답변으로 처리된다.

## OpenAI API 키 처리

API 키는 코드에 하드코딩하지 않고 `OPENAI_API_KEY` 환경변수로 읽는다. 프로젝트 루트의 `.env` 파일은 `python-dotenv`로 자동 로드되며, `.env.example`은 실제 키 없이 설정 형식만 제공하는 템플릿이다. `.env`는 `.gitignore`에 포함되어 GitHub에 업로드되지 않도록 구성했다. 서버 시작 시 키가 없으면 경고 로그를 출력하고, `/chat` 또는 `/integrated-chat` 호출 시 명확한 detail 메시지를 반환한다.

## Frontend 전송 UX 개선

Frontend 디자인과 HTML/CSS 구조는 변경하지 않았다. `script.js`에서 기존 전송 버튼 클릭 동작을 유지하면서, 입력창에서 Enter를 누르면 메시지가 전송되도록 했다. Shift+Enter는 줄바꿈 동작을 유지하도록 처리했다. 또한 요청이 진행 중일 때 중복 전송이 발생하지 않도록 전송 버튼을 임시로 disabled 처리하고, 응답 완료 후 다시 활성화한다.

## 유사도 검색의 장점과 한계

유사도 검색은 키워드가 정확히 일치하지 않아도 의미적으로 가까운 문서를 찾을 수 있다는 장점이 있다. 예를 들어 “DB 조회가 느린 이유”와 “인덱스 성능”처럼 표현이 달라도 관련 chunk를 검색할 수 있다. 한계는 embedding 품질과 chunk 품질에 따라 검색 정확도가 달라진다는 점이다. threshold를 높이면 부정확한 RAG 사용은 줄어들지만, 일부 관련 문서를 놓칠 수도 있다.

## 데이터 품질이 응답에 미치는 영향

RAG 답변은 검색된 context의 품질에 크게 의존한다. 문서가 잘 구조화되어 있고 제목이 명확할수록 section metadata와 검색 결과가 좋아진다. 반대로 빈 파일, 깨진 PDF, 중복 문서, 너무 긴 문단은 검색 정확도를 낮출 수 있다. 따라서 CS 문서는 주제별 폴더와 명확한 Markdown heading 구조로 관리하는 것이 중요하다.

## 개선 방향

- 문서 변경 감지 기반 증분 인덱싱 추가
- chunk별 token 수 기반 분할 도입
- reranker 모델을 이용한 검색 결과 재정렬
- 답변 화면에 source와 section을 자연스럽게 표시
- 관리자용 문서 업로드/삭제 UI 개선
- 테스트 코드와 API 자동 검증 스크립트 추가

## 프로젝트 수행 후기

- 서현식
스켈레톤의 간단한 챗봇 구조를 유지하면서 Backend 중심으로 RAG 파이프라인을 완성했다. `os.walk()` 기반 재귀 탐색과 Markdown section metadata 저장을 통해 현재 data 폴더 구조를 그대로 활용할 수 있게 만들었다. 이후 threshold 강화와 fallback 정책을 적용하면서, 관련성이 낮은 문서를 억지로 참고하는 문제를 줄였다. 마지막으로 답변 스타일을 자연스럽게 조정해 문서 요약기보다는 실제 CS 면접 챗봇에 가까운 사용감을 만들었다.
