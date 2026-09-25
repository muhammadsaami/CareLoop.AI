# CareLoop AI — Backend

## Project
**CareLoop AI** is an AI-powered post-hospital-discharge recovery and compliance assistant designed to assist patients and caregivers during recovery by managing medications, follow-up appointments, symptom check-ins, and medical warning signs.

---

## Current Phase
**Phase 2 — Discharge Document Ingestion (complete)**

Phase 1 (listed below) remains fully supported and its tests still pass.

### Phase 1 — Production Backend Foundation
- FastAPI modular web application with `/api/v1/` route versioning
- PostgreSQL database engine with connection pooling and safe transactions
- SQLAlchemy 2.x ORM models with UUID primary keys and timezone-aware timestamps
- Alembic database migrations
- Pydantic v2 schemas for request validation and ORM serialization
- Repository and Service layers enforcing business constraints and preventing orphan records
- Safe deletion boundaries to protect patient records
- Comprehensive automated test suite with isolated test database
- Structured, privacy-conscious logging (no PII, credentials, or sensitive health data in logs)

### Phase 2 — Discharge Document Ingestion
- **Secure upload** of PDF/PNG/JPG/JPEG with triple validation: extension, declared content type, and magic-byte sniffing
- **Isolated local storage** with UUID filenames; the client filename is never used to build a path, and storage can never resolve inside `app/`
- **Atomic writes** so a partial file is never visible under its final name
- **Duplicate protection** via SHA-256 unique per `(patient_id, sha256_hash)`; duplicates return `409` and remove the redundant copy
- **Text extraction** with PyMuPDF, automatically falling back to Tesseract OCR page-by-page for scanned documents
- **Text normalisation** that removes OCR noise without altering clinical content
- **Structured extraction** of medications, appointments, and warning symptoms into the existing Phase 1 tables
- **Provider abstraction** (`LLMProvider`) with Groq and Gemini adapters behind one interface, `temperature=0`
- **Safety validation** applied independently of the model: nothing is inferred, placeholders become null, and model-authored clinical advice is flagged rather than trusted
- **Audit trail** — every run writes an `extraction_runs` row recording provider, model, and outcome
- **Secret redaction** on all outgoing error messages

### Phase 2 endpoints
| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/discharge-documents` | Upload and process a discharge document |
| `GET` | `/api/v1/discharge-documents/{id}` | Fetch one document with its extraction audit trail |
| `GET` | `/api/v1/patients/{id}/discharge-documents` | List a patient's documents |
| `POST` | `/api/v1/discharge-documents/{id}/reprocess` | Re-run the pipeline on a stored document |

`patient_id` must reference an **existing** patient. A document never creates one.

### Phase 2 error semantics
| Status | Meaning |
| --- | --- |
| `400` | Unsupported file type, or extension/content-type mismatch |
| `404` | Patient or document not found |
| `409` | Exact duplicate already uploaded for this patient |
| `413` | File exceeds `MAX_UPLOAD_SIZE_MB` |
| `422` | Corrupted/unreadable document, or a document with no extractable text |
| `429` | Extraction provider rate limit |
| `502` | Provider returned an error or malformed output |
| `503` | Tesseract or the LLM provider is not configured |
| `504` | Extraction provider timed out |

A missing Tesseract install is a **503**, not a 422: it is a server
misconfiguration, and reporting it as a corrupt document would wrongly blame
the uploader for a client-side file that is perfectly fine.

---

## Requirements
* **Python**: 3.11+
* **PostgreSQL**: 15+ (PostgreSQL 17 supported)
* **Git**
* **Tesseract OCR** (Phase 2, optional): only needed for scanned PDFs and
  image uploads. Native binary, not a pip package.
  ```powershell
  winget install UB-Mannheim.TesseractOCR
  # then either add to PATH or set TESSERACT_CMD
  $env:TESSERACT_CMD="C:/Program Files/Tesseract-OCR/tesseract.exe"
  ```
* **An LLM API key** (Phase 2, optional): Groq or Gemini. Only the selected
  provider needs a key. Without one, uploads still store and extract text,
  but structured extraction returns `503`.

---

## Setup Instructions

### 1. Virtual Environment (Windows PowerShell)

```powershell
# From the backend directory
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Environment Configuration
Copy the template configuration file:
```powershell
Copy-Item .env.example .env
```

Edit `.env` with your PostgreSQL credentials:
```env
APP_NAME=CareLoop AI
ENVIRONMENT=development

DATABASE_URL=postgresql+psycopg://postgres:your_password@localhost:5432/careloop
TEST_DATABASE_URL=postgresql+psycopg://postgres:your_password@localhost:5432/careloop_test

SECRET_KEY=generate-a-secure-random-key-for-jwt-32-chars-min
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

Add the Phase 2 settings (see `.env.example` for the full annotated list):
```env
# Where uploads are written. Relative paths are anchored to backend/.
DOCUMENT_STORAGE_PATH=storage/discharge_documents
MAX_UPLOAD_SIZE_MB=10
MAX_DOCUMENT_PAGES=30

# Leave blank to use the system PATH.
TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe
OCR_LANGUAGE=eng

# Exactly one provider is used; only that one needs a key.
LLM_PROVIDER=groq
GROQ_API_KEY=
LLM_TIMEOUT_SECONDS=60
```

---

## PostgreSQL Database Setup

1. Start the PostgreSQL service if not already running:
   ```powershell
   Start-Service postgresql-x64-17
   ```
2. Create the development and test databases:
   ```powershell
   psql -U postgres -c "CREATE DATABASE careloop;"
   psql -U postgres -c "CREATE DATABASE careloop_test;"
   ```
3. Verify that `DATABASE_URL` in `.env` connects to `careloop`.

---

## Database Migrations (Alembic)

Run all pending migrations to apply the schema:
```bash
alembic upgrade head
```

To create a new migration in future phases:
```bash
alembic revision --autogenerate -m "description_of_changes"
```

**Never edit a migration that has already been applied.** Generate a new
one instead — the Phase 2 migration `a808a053b916` builds on the Phase 1
migration `dc558334188b` and must remain in that order.

> Enum columns are generated with `values_callable`, so PostgreSQL stores
> lowercase **values** (`discharge_summary`, `pending`), matching Phase 1.
> Autogenerate defaults to storing uppercase member names, which is
> inconsistent with the existing tables — check the generated file.

---

## Running the Application

Start the development server with live reload:
```bash
uvicorn app.main:app --reload --port 8000
```

The API will be accessible at:
- **Base URL**: `http://127.0.0.1:8000`
- **Health Check**: `http://127.0.0.1:8000/api/v1/health`
- **Interactive Swagger Docs**: `http://127.0.0.1:8000/docs`
- **ReDoc Documentation**: `http://127.0.0.1:8000/redoc`

### Trying the upload endpoint
```powershell
# 1. Create a patient first - documents never create patients.
$patient = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/patients" `
  -ContentType "application/json" `
  -Body '{"name":"Jane Doe","contact_number":"+1-555-0199","discharge_date":"2026-09-20"}'

# 2. Upload a discharge summary.
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/discharge-documents" `
  -Form @{ patient_id = $patient.id; file = Get-Item .\summary.pdf }
```

The response contains counts and created record ids only — never the
document text.

---

## Running Automated Tests

Run the full pytest suite:
```bash
pytest -v
```

Tests automatically run against the isolated `TEST_DATABASE_URL` database and truncate tables between tests to ensure test isolation without touching the development database.

The Phase 2 suite needs **no Tesseract install and no API key**: OCR and the
LLM provider are replaced with test doubles, and uploads are redirected to a
temporary directory. This keeps the suite hermetic and free of network calls.

---

## Architecture

The project enforces a strict, clean separation of concerns:

```text
HTTP Request
     ↓
FastAPI Routes (/api/v1/)
     ↓
Service Layer (Business Logic & Validation)
     ↓
Repository Layer (Data Access & Queries)
     ↓
SQLAlchemy 2.x ORM Models
     ↓
PostgreSQL
```

### Modules:
- `app/core/`: Configuration (`config.py`), database engine (`database.py`), logging (`logging.py`), domain exceptions (`exceptions.py`), secret redaction (`redaction.py`), security stubs (`security.py`).
- `app/models/`: SQLAlchemy 2.x declarative models (`patient`, `medication`, `appointment`, `warning_symptom`, `checkin`, `adherence_log`, `discharge_document`, `extraction_run`).
- `app/schemas/`: Pydantic v2 schemas (`Create`, `Update`, `Response`, plus the Phase 2 extraction contract).
- `app/repositories/`: Direct database queries, isolating SQLAlchemy session interactions.
- `app/services/`: Application workflows, ownership verification, cascading checks, transactions. Phase 2 adds `storage`, `text_processing`, `ocr`, `document_processing`, `extraction`, `safety`, and `discharge_document`.
- `app/llm/`: `LLMProvider` abstraction with `groq_provider` and `gemini_provider` adapters and a `factory`.
- `app/api/`: Versioned API endpoints (`/api/v1/...`).

### Phase 2 pipeline
```text
upload bytes
   → storage.validate_upload  (extension + content type + magic bytes)
   → storage.store            (atomic write, UUID name, SHA-256)
   → duplicate check          (patient_id + sha256 → 409)
   → document_processing      (text layer, else per-page OCR)
   → text_processing          (noise removal, content preserved)
   → llm provider             (groq | gemini, temperature 0)
   → safety.SafetyValidator   (independent of the model)
   → persist Phase 1 records
   → extraction_runs audit row
```

### Phase 2 tables
- `discharge_documents` — one row per upload: server-side filename, SHA-256,
  processing/OCR/extraction status, page count, extracted text.
- `extraction_runs` — one row per extraction attempt: provider, model,
  status, character count, and the sanitised error summary.

`discharge_documents` uses `ON DELETE RESTRICT` for the patient and
`extraction_runs` uses `ON DELETE CASCADE` for the document.

---

## Healthcare Safety Boundaries

CareLoop AI is **strictly a transcription and data storage system**.
- It does **NOT** provide medical diagnoses.
- It does **NOT** recommend treatments or dosages.
- It does **NOT** evaluate emergency or danger status.
- Warning symptoms and check-in flag statuses represent stored patient-reported records only.

Phase 2 adds an LLM, which raises the stakes, so the following is enforced in
`app/services/safety.py` **independently of the model**:

- Values are **transcribed**, never inferred. A missing dosage stays `null`;
  it is never computed, guessed, or filled in.
- Placeholder strings (`N/A`, `not specified`, `unknown`, …) are treated as
  missing so they are never persisted as if a clinician had written them.
- Items with no usable identifying value are **dropped** rather than stored
  as meaningless records.
- An impossible `source_page` is cleared and the item flagged.
- Text where the model appears to have **authored clinical advice**
  ("I recommend…", "call 911", "increase the dose") is **flagged for review,
  not rewritten** — altering clinical text is out of bounds.
- **Every** warning symptom is stored flagged `REQUIRES REVIEW` with its
  source snippet, so a human verifies it before it is relied upon.
- A document containing warning symptoms is reported as `needs_review`
  rather than `completed`.

Because the model is treated as untrusted, the prompt asks it to behave
safely but the code does not depend on that.

---

## Known Limitations
- **Hyphenation repair** joins any word split across a line, so a genuine
  compound wrapped after its hyphen (`beta-\nblocker`) becomes
  `betablocker`. Distinguishing the two needs a dictionary of known
  medication names. The trade-off is deliberate: the alternative leaves
  every wrapped medication split, which corrupts more names.
- **OCR accuracy** depends entirely on scan quality. Text-layer PDFs are
  exact; scanned pages are best-effort. Both are auditable via `source_text`.
- **Storage is local disk.** There is no object store, no virus scanning of
  uploads, and no encryption at rest beyond the filesystem's own.

---

## Future Phases

```text
Phase 3 — Medical Knowledge Base + RAG
Phase 4 — LangGraph Agent Workflow
Phase 5 — Scheduler + WhatsApp
Phase 6 — Daily Check-in + Escalation
Phase 7 — React Dashboard
Phase 8 — Security, Testing, Deployment
```
