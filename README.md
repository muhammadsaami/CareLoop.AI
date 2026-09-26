# CareLoop AI

AI-powered post-hospital-discharge recovery and compliance assistant.

---

## Current Status
**Phase 3 — Grounded RAG Retrieval (Completed & Verified)**

Index the text extracted in Phase 2 into ChromaDB and retrieve **verbatim
source passages** with the page each came from. Retrieval only — no answer
generation, no clinical guidance.

For comprehensive backend setup instructions, architecture documentation, and testing guides, see:
👉 [Backend README](backend/README.md)

---

## Phase 3 at a glance

| Concern | How it is handled |
| --- | --- |
| Chunking | Page-scoped from the existing `--- PAGE n ---` markers; **never** spans a page, so `source_page` is exact and never guessed |
| Boundaries | Paragraph → sentence → word, with a floor that stops a snapped boundary from collapsing the forward stride |
| Verbatim text | Only the cut points are chosen; no word is rewritten, reordered, summarised, or dropped |
| Determinism | Chunk IDs are `{document_id}:{page}:{index}`; re-indexing overwrites instead of duplicating |
| Vectors | `EmbeddingProvider` abstraction; the default is a real **semantic** sentence embedder (`BAAI/bge-small-en-v1.5`, 384-d) run **locally** on CPU via ONNX Runtime — no API key, no outbound call, no PHI leaving the host |
| Storage | One ChromaDB collection; every chunk carries `patient_id` and `discharge_document_id` metadata |
| Tenancy | Mandatory `where` filter on **both** IDs on every read and delete, plus a PostgreSQL ownership check before any vector-store call |
| Not-found vs not-yours | Identical 404 body, so document IDs cannot be enumerated |
| Relevance floor | Chunks below the selected provider's calibrated score are dropped, so a query the document never mentions returns **empty**, not its least-unrelated passage |
| Indexing | Explicit `POST .../index`; uploading a document does **not** silently index it |
| Secrets & PHI | Query text, passages, and filenames are never logged; only counts, scores, and IDs are |

### Endpoints

```
POST /api/v1/rag/retrieve                          search one document
POST /api/v1/rag/documents/{document_id}/index     index one document
```

### Safety boundary

Phase 3 **retrieves text a clinician already wrote** and stops there. There is
no LLM call anywhere in `app/rag/`. The API deliberately has no `answer`,
`summary`, or `recommendation` field, and a test fails if one is ever added.

A citation is only as trustworthy as its page number, which is why chunks are
never allowed to cross a page boundary and an unknown page is reported as
`null` rather than guessed.

### Known limitations

- Semantic retrieval finds meaning, not just shared words. With the default
  model, "blood thinner" matches a document that only says "warfarin … to thin
  your blood", and "water tablet" finds paracetamol.
- The lexical `hashing` provider is still available for an air-gapped or
  model-free install. Set `RAG_EMBEDDING_PROVIDER=hashing`; it matches shared
  words only, so it will not do the above.
- **Do not mix providers in one index.** Vectors from different models are not
  comparable, and neither are vectors of different widths. Changing provider or
  dimension means deleting `careloop_documents` and re-indexing every document.
- A query sharing only a *generic* word with the document (e.g. "medication"
  in a question about an unrelated drug) may still be returned with a moderate
  score. The score is exposed to the caller so it can decide, rather than
  hidden.

---

## Phase 2 at a glance

| Concern | How it is handled |
| --- | --- |
| File validation | Extension **and** content-type **and** magic-byte sniffing; size and page limits |
| Storage | UUID filenames outside the `app/` package; the uploaded name is never used as a path |
| Traversal | `path_for()` is the only way to a path and rejects anything outside the storage root |
| Duplicates | SHA-256 unique per `(patient_id, hash)`; a duplicate returns `409` and leaves no orphan file |
| Text extraction | PyMuPDF text layer, falling back to Tesseract OCR per page |
| Structured extraction | Groq or Gemini behind one `LLMProvider` interface, at `temperature=0` |
| Safety | Values are transcribed verbatim, never inferred; placeholders become null; over-reach phrasing is flagged, not rewritten |
| Audit | Every run writes an `extraction_runs` row with provider, model, and outcome |
| Secrets | API keys are never logged or returned; outgoing errors pass through redaction |

### Safety boundary

This pipeline **transcribes what a clinician already wrote**. It does not
diagnose, recommend, adjust doses, or assess emergency status. Every
warning symptom is stored flagged `REQUIRES REVIEW` with its source snippet
so a human can verify it.

---

## Quick Start (Windows PowerShell)

```powershell
# Navigate to backend
cd backend

# Setup environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Run tests
pytest -v

# Start development server
uvicorn app.main:app --reload
```

Interactive API documentation will be available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### Phase 2 prerequisites

```powershell
# Tesseract is a native binary, not a pip package.  Install it, then either
# add it to PATH or point TESSERACT_CMD at the executable.
#   winget install UB-Mannheim.TesseractOCR
#   $env:TESSERACT_CMD="C:/Program Files/Tesseract-OCR/tesseract.exe"

# Configure one extraction provider in backend/.env
#   LLM_PROVIDER=groq
#   GROQ_API_KEY=gsk_...
```

Text-layer PDFs work without Tesseract. Scanned PDFs and image uploads
return `503` with an actionable message until Tesseract is configured.

### Phase 3 prerequisites

The default embedding provider is a real sentence-embedding model that runs
locally, so the weights (~127 MB) must be fetched **once** during deployment:

```powershell
cd backend
python -m app.rag.embeddings.fetch_model
```

This is an explicit operator step. The application never downloads it at
runtime, so a request can never cause a network call, and the provider needs
no API key. After fetching you can set `RAG_SEMANTIC_ALLOW_DOWNLOAD=false` to
make that explicit.

To skip the model entirely, set `RAG_EMBEDDING_PROVIDER=hashing` for a
lexical, model-free install. The full test suite passes either way: tests use
a deterministic fake provider and never download or call a network service.

---

## Roadmap

- [x] **Phase 1** — Production Backend Foundation
- [x] **Phase 2** — Discharge Summary OCR + Extraction
- [x] **Phase 3** — Grounded RAG Retrieval (ChromaDB, retrieval only)
- [ ] **Phase 4** — LangGraph Agent Workflow
- [ ] **Phase 5** — Scheduler + WhatsApp
- [ ] **Phase 6** — Daily Check-in + Escalation
- [ ] **Phase 7** — React Dashboard
- [ ] **Phase 8** — Security, Testing, Deployment
