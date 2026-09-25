# CareLoop AI

AI-powered post-hospital-discharge recovery and compliance assistant.

---

## Current Status
**Phase 2 — Discharge Document Ingestion (Completed & Verified)**

Upload a discharge summary (PDF/PNG/JPG), extract its text (with OCR for
scanned pages), and have an LLM pull out structured medications,
appointments, and warning symptoms into the Phase 1 tables.

For comprehensive backend setup instructions, architecture documentation, and testing guides, see:
👉 [Backend README](backend/README.md)

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

---

## Roadmap

- [x] **Phase 1** — Production Backend Foundation
- [x] **Phase 2** — Discharge Summary OCR + Extraction
- [ ] **Phase 3** — Medical Knowledge Base + RAG
- [ ] **Phase 4** — LangGraph Agent Workflow
- [ ] **Phase 5** — Scheduler + WhatsApp
- [ ] **Phase 6** — Daily Check-in + Escalation
- [ ] **Phase 7** — React Dashboard
- [ ] **Phase 8** — Security, Testing, Deployment
