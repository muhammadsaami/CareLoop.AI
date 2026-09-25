# CareLoop AI

AI-powered post-hospital-discharge recovery and compliance assistant.

---

## Current Status
**Phase 1 — Production Backend Foundation (Completed & Verified)**

For comprehensive backend setup instructions, architecture documentation, and testing guides, see:
👉 [Backend README](backend/README.md)

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

---

## Roadmap

- [x] **Phase 1** — Production Backend Foundation
- [ ] **Phase 2** — Discharge Summary OCR + Extraction
- [ ] **Phase 3** — Medical Knowledge Base + RAG
- [ ] **Phase 4** — LangGraph Agent Workflow
- [ ] **Phase 5** — Scheduler + WhatsApp
- [ ] **Phase 6** — Daily Check-in + Escalation
- [ ] **Phase 7** — React Dashboard
- [ ] **Phase 8** — Security, Testing, Deployment
