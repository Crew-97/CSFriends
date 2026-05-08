# RAG Architecture

```text
User
  |
  v
Frontend script.js
  |
  v
FastAPI /integrated-chat
  |
  +--> SentenceTransformer query embedding
  |
  +--> ChromaDB vector search
  |
  +--> Context assembly with source metadata
  |
  +--> OpenAI Chat Completions
  |
  v
JSON answer + used_rag + sources
```

## Indexing Flow

```text
servers/data
  |
  v
os.walk recursive traversal
  |
  v
md/txt/pdf text extraction
  |
  v
Markdown section split + fallback chunking
  |
  v
SentenceTransformer embeddings
  |
  v
servers/chroma_data persistent collection
```
