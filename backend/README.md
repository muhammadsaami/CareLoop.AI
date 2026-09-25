# CareLoop AI — Backend

## Project
**CareLoop AI** is an AI-powered post-hospital-discharge recovery and compliance assistant designed to assist patients and caregivers during recovery by managing medications, follow-up appointments, symptom check-ins, and medical warning signs.

---

## Current Phase
**Phase 1 — Production Backend Foundation**

This phase delivers the core infrastructure:
- FastAPI modular web application with `/api/v1/` route versioning
- PostgreSQL database engine with connection pooling and safe transactions
- SQLAlchemy 2.x ORM models with UUID primary keys and timezone-aware timestamps
- Alembic database migrations
- Pydantic v2 schemas for request validation and ORM serialization
- Repository and Service layers enforcing business constraints and preventing orphan records
- Safe deletion boundaries to protect patient records
- Comprehensive automated test suite with isolated test database
- Structured, privacy-conscious logging (no PII, credentials, or sensitive health data in logs)

---

## Requirements
* **Python**: 3.11+
* **PostgreSQL**: 15+ (PostgreSQL 17 supported)
* **Git**

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

Run all pending migrations to apply the initial schema:
```bash
alembic upgrade head
```

To create a new migration in future phases:
```bash
alembic revision --autogenerate -m "description_of_changes"
```

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

---

## Running Automated Tests

Run the full pytest suite:
```bash
pytest -v
```

Tests automatically run against the isolated `TEST_DATABASE_URL` database and truncate tables between tests to ensure test isolation without touching the development database.

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
- `app/core/`: Configuration (`config.py`), database engine (`database.py`), logging (`logging.py`), security stubs (`security.py`).
- `app/models/`: SQLAlchemy 2.x declarative models (`patient`, `medication`, `appointment`, `warning_symptom`, `checkin`, `adherence_log`).
- `app/schemas/`: Pydantic v2 schemas (`Create`, `Update`, `Response`).
- `app/repositories/`: Direct database queries, isolating SQLAlchemy session interactions.
- `app/services/`: Application workflows, ownership verification, cascading checks, transactions.
- `app/api/`: Versioned API endpoints (`/api/v1/...`).

---

## Healthcare Safety Boundaries

CareLoop AI Phase 1 is **strictly an infrastructure and data storage foundation**.
- It does **NOT** provide medical diagnoses.
- It does **NOT** recommend treatments or dosages.
- It does **NOT** evaluate emergency or danger status.
- Warning symptoms and check-in flag statuses represent stored patient-reported records only.

---

## Future Phases

```text
Phase 2 — Discharge Summary OCR + Extraction
Phase 3 — Medical Knowledge Base + RAG
Phase 4 — LangGraph Agent Workflow
Phase 5 — Scheduler + WhatsApp
Phase 6 — Daily Check-in + Escalation
Phase 7 — React Dashboard
Phase 8 — Security, Testing, Deployment
```
