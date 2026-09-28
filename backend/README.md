# CareLoop AI — Backend

## Project
**CareLoop AI** is an AI-powered post-hospital-discharge recovery and compliance assistant designed to assist patients and caregivers during recovery by managing medications, follow-up appointments, symptom check-ins, and medical warning signs.

---

## Current Phase
**Phase 6 — Daily Check-ins and Safe Escalation (complete)**, plus
authentication and patient-level authorization across the whole surface: see
[Authentication and Authorization](#authentication-and-authorization). Every
route except the two health probes requires a signed bearer token, and every
patient-scoped route requires a per-patient grant. That closed the gap earlier
phases documented - they scoped rows correctly, but had no identity to scope
*for*.

Phase 1–5 (listed below) remain fully supported and their tests still pass.
Phase 6 is strictly **additive** to check-ins: the free-text check-ins from
Phase 1 keep their exact behaviour, and the new structured check-ins live
alongside them in the same table rather than replacing them. The two migrations
it adds are fourth and fifth in the chain.

### Phase 6 — Daily Check-ins and Safe Escalation

A patient answers a small, **coded** set of questions about their own
documented warning symptoms. Two published rules read those answers and may
raise an escalation for a human to look at. Nothing infers a symptom the
patient did not select, and nothing sends anything by default.

#### Two shapes of check-in, one table

`checkins` now holds both record types, told apart by `responses`:

| | `response_text` (Phase 1) | `responses` (Phase 6) |
| --- | --- | --- |
| Shape | free text, `flagged`, `flag_reason` | JSON of coded answers |
| Nullability | `response_text` became **nullable** | `responses` nullable |
| Which endpoints see it | `GET /checkins`, `GET /checkins/{id}` | everything under `/checkins/daily` |
| What can escalate it | nothing, ever | the two published rules |

A structured row always has `responses` set and `response_text` null; a legacy
row always has `response_text` set and `responses` null. The repository filters
on that discriminator in both directions, so a Phase 1 client listing check-ins
never sees a structured blob it cannot render, and the Phase 6 history endpoint
never returns a legacy row with `responses: null` that looks like a failed
evaluation.

Both shapes share **one** unique constraint on `(patient_id, date)`. A patient
can have exactly one check-in per day regardless of which endpoint recorded it,
which is the property the daily check-in actually depends on.

#### Answers are codes, and the codes are checked against this patient

A submission looks like this — no free text field exists:

```json
{
  "date": "2026-09-28",
  "general_wellbeing": "okay",
  "condition_change": "same",
  "warning_symptoms": [
    {"symptom_id": "<uuid from /checkins/questions>", "change": "worse"}
  ]
}
```

`symptom_id` is **not** a free reference. Every id is looked up with a patient
filter before it can reach the evaluator, so an id belonging to another patient
is indistinguishable from one that does not exist. This is the single most
important line of defence in the phase, and it is structural: `build_reports()`
only accepts ids found in a mapping the caller loaded *with a patient filter*,
so there is no code path that hands an unverified id to a rule.

The evaluator is a pure function of `(stored facts, config)` — no clock, no
model, no network. Two submissions with the same answers produce the same
escalations in the same order, which is what makes the `(checkin_id,
rule_code)` idempotency constraint meaningful rather than decorative.

#### The published rules

Both rules are anchored on warning symptoms the patient **already has on
record** from their discharge instructions. Neither fires on a symptom the
patient does not have, and neither reads free text.

| Rule code | Category | Fires when | Floor |
| --- | --- | --- | --- |
| `WARNING_SYMPTOM_WORSENED` | `warning_criteria_changed` | A documented symptom is reported `worse` | **Ignored** — always `low` |
| `WARNING_SYMPTOM_AT_SEVERITY_FLOOR` | `warning_criteria_present` | A documented symptom is reported `worse`, `same`, or `better`, at or above the floor | Honours `CHECKIN_SEVERITY_FLOOR` |

Both are versioned (`checkin-red-flags-v1`) and the version is stored on every
escalation row alongside the rule code, so an audit can be replayed against the
exact rule set that produced it.

#### The severity floor is asymmetric, on purpose

`CHECKIN_SEVERITY_FLOOR` can be **raised** above what a rule declares, making
that rule stricter. It can never be lowered, because configuration must not be
able to invent a clinical rule more sensitive than the one that was reviewed.

It applies to the floor rule **only**. A worsening is a change over time, not an
absolute severity, so `honours_deployment_floor=False` keeps it firing at every
documented severity. Without that exemption,
`CHECKIN_SEVERITY_FLOOR=critical` would silence worsening alerts for every
documented symptom below critical — including a high-severity symptom that got
worse. A clinical signal would disappear silently, in the direction nobody is
watching, because a configuration knob was turned. The exemption is declared
per rule rather than special-cased in the evaluator, so the published set reads
in one place and a new rule must make the choice explicitly.

#### Unevaluable is not safe

`needs_review` is recorded independently of escalation, because a check-in can
both escalate and need review, and collapsing the two would lose a fact.

| Review code | Meaning |
| --- | --- |
| `unmapped_distress` | Patient said `unwell` / `very_unwell` / `worse` but selected no warning symptom. There is nothing to match, and this is **not** "safe". |
| `unrecognised_warning_symptom` | A symptom id is not one of this patient's. Dropped from evaluation, not guessed at. |
| `unknown_symptom_severity` | A stored severity cannot be ranked, so the floor cannot be applied. Skipped, not guessed low. |
| `incomplete_responses` | The answer set was empty. |

`unmapped_distress` is the case the phase is most careful about: the patient may
be telling us something is wrong and we have no documented criterion to compare
it against. Rounding that to either "safe" or "escalated" would be inventing a
clinical judgement, so it is handed to a human.

#### Escalation lifecycle

```
pending ──delivery succeeds──► notified ──human──► acknowledged ──► resolved
   └────────────human──────► cancelled (should not have been raised)
```

Transitions are one-way and refuse to go backwards
(`EscalationTransitionError`, `409`). The automatic `pending → notified` edge is
the only one the system takes by itself, and it happens on confirmed delivery —
never on materialising a notification, because a row in the outbox is not a
message somebody received.

That is also why `acknowledge` is **not** reachable from `pending`: it means "a
human has seen that this was communicated", which is not true until the notice
has gone out. On an escalation nobody has been notified about yet, the only
human action available is `cancel`.

A late retry therefore cannot overwrite a human decision: if a caregiver
notice finally delivers after a clinician has already `resolved` the
escalation, the status stays `resolved` and only `notified_at` moves.

#### Caregiver notification is opt-in, and has no fallback

Notifications are requested only when **both** `CHECKIN_ESCALATION_ENABLED` and
`CHECKIN_NOTIFY_CAREGIVER` are on, and are delivered through the **existing**
Phase 5 pipeline — same outbox, same providers, same retry budget. Phase 6 adds
no second send path, because two would mean two retry policies and two places
for a duplicate caregiver alert to be born.

The recipient is `Patient.caregiver_contact` and nothing else. If that field is
empty the escalation is **still created and still reviewable in the API**; it
records `notification_blocked_reason` and notifies nobody. The patient's own
number is never substituted, because a caregiver notice and a patient prompt
are different messages to different people with different consent.

#### Recovery

`careloop.notify_pending_escalations` runs every minute and materialises
notifications for escalations that have none. It exists for the one case the
request path cannot cover: a submission that committed an escalation row while
the outbox write failed, or an operator enabling the pathway after the fact.
It only **materialises**; delivery stays with Phase 5.

#### Configuration

Every Phase 6 setting is inert by default, so a fresh deployment records and
evaluates check-ins, creates no escalation, and sends nothing. See
[`.env.example`](.env.example) for the annotated list.

| Setting | Default | Notes |
| --- | --- | --- |
| `CHECKIN_ESCALATION_ENABLED` | `false` | Master switch. Off ⇒ record and evaluate, never act. |
| `CHECKIN_NOTIFY_CAREGIVER` | `false` | Separate from the master switch: a notice discloses to a third party. Enabling it while the pathway is off is a startup error. |
| `CHECKIN_PROMPT_LOCAL_TIME` | `09:00` | Naive local time — the prompt is sent in the **patient's** timezone from the patient record, so one global setting cannot get it wrong. |
| `CHECKIN_ENABLED_RULES` | blank | Blank means *all published rules*, not none. An unknown code is a startup error, so a typo can never read as "no rules active". |
| `CHECKIN_SEVERITY_FLOOR` | `low` | Raise only. Applies to the floor rule only — see above. |
| `CHECKIN_MAX_SYMPTOM_REPORTS` | `20` | Bounded 1–200; the answers are JSON in a clinical record. |

#### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/patients/{id}/checkins/questions` | Question set + this patient's warning symptoms |
| `POST` | `/patients/{id}/checkins/daily` | Submit today's structured check-in and evaluate it |
| `GET` | `/patients/{id}/checkins/daily/latest` | Most recent structured check-in |
| `GET` | `/patients/{id}/checkins/daily/{id}` | One structured check-in |
| `GET` | `/patients/{id}/checkins/daily` | Paged history, `skip`/`limit` |
| `POST` | `/patients/{id}/checkins/schedule` | Set up (or fetch) the daily prompt reminder |
| `GET` | `/patients/{id}/escalations` | Newest first; `status`, `skip`, `limit` |
| `GET` | `/patients/{id}/escalations/{id}` | One escalation |
| `POST` | `/patients/{id}/escalations/{id}/acknowledge` | Human has seen it |
| `POST` | `/patients/{id}/escalations/{id}/resolve` | Close it out, with an optional note |
| `POST` | `/patients/{id}/escalations/{id}/cancel` | It should not have been raised |

Submitting twice for one date returns `CheckInAlreadySubmittedError`
(`409`), including when two requests race and the database — not the
application — is what rejects the second.

#### What Phase 6 does not do

- It does not infer a symptom from free text, and it does not read
  `response_text` for anything.
- It does not diagnose, triage, advise, or suggest a treatment or medication
  change. `patient_message` and notification bodies are fixed strings that name
  a workflow, never a clinical instruction.
- It does not escalate a symptom the patient does not have on record.
- It does not notify anyone by default, and it does not fall back to the
  patient's own number when no caregiver contact exists.
- It does not log response text, symptom descriptions, or patient answers.
  `reason_code` carries the rule code, the rule version, and a severity
  **label** — nothing else — because that is what ends up in logs.
- It requires a bearer token and a per-patient grant on every route, and a
  cross-patient id is refused with `403`. See
  [Authentication and Authorization](#authentication-and-authorization).
- It does not capture caregiver consent. `Patient.consent_*` still does not
  exist, so `CHECKIN_NOTIFY_CAREGIVER` is an operator setting, not a
  consent record.

### Phase 5 — Scheduling and Notifications

A recurring-reminder engine that turns a clinician's explicit dose times into
per-occurrence notifications, delivered by a Celery worker through a pluggable
provider.

#### The two rows

| Row | Granularity | Mutable |
| --- | --- | --- |
| `reminders` | One **rule** — a schedule, not a moment | Yes: pause, resume, re-window, cancel |
| `notifications` | One **occurrence** — a single moment | No: status and attempt count only |

A rule carries `local_time` + `timezone` and computes the next occurrence. An
occurrence is materialised ahead of time with a rendered body and a delivery
status. Separating them is what makes "pause the 20:00 dose but keep the 08:00
one" expressible.

#### Times are never inferred

`Medication.frequency` is free text (`"twice daily with meals"`). Nothing in
Phase 5 parses it. Creating a medication reminder requires explicit `times`:

```
POST /api/v1/patients/{id}/reminders/medication
{"medication_id": "...", "times": ["08:00", "20:00"]}
```

The request model sets `extra="forbid"`, so a `schedule` or `frequency` field is
**rejected** rather than ignored. A caller who supplies prose and receives a 201
would believe the schedule was honoured.

When the supplied times contradict `frequency` (one time supplied, frequency
implying two), the supplied schedule is used and the reminder is flagged
`needs_review` with a log line. The system does not silently correct a
clinician, and does not silently drop their instruction.

#### Appointment times come from the appointment

An appointment reminder takes only a lead time. The instant is derived from
`Appointment.date`, which already exists:

```
POST /api/v1/patients/{id}/reminders/appointment
{"appointment_id": "...", "lead_time_minutes": 60}
```

A `date` in the request is rejected. Two copies of the same fact would let them
disagree, and the caller's copy would be the one silently ignored.

#### Idempotency

Every notification carries a deterministic key derived from
`(reminder_id, occurrence UTC instant, channel)`, protected by a database
UNIQUE index. Materialising the same occurrence twice returns the existing row.

This is what makes Celery's at-least-once delivery safe. A worker killed after
sending but before acknowledging re-runs the task, finds the key, and does
nothing. The guarantee is in the database, not in Celery — the result backend is
deliberately not involved (`task_ignore_result=True`).

#### Overdue occurrences are skipped, not sent

If the worker was down and an occurrence passed un-sent, it is **not** delivered
late. A medication prompt hours overdue risks a double dose: the patient has
probably already taken it, or deliberately skipped it. The occurrence is
retired, the rule is advanced past **every** missed occurrence in one pass, and
the skip is counted. Reconciliation is idempotent.

#### Timezones and DST

Times are stored as a local wall-clock time plus an IANA zone, and every
occurrence is persisted as an aware UTC instant. `Patient.timezone` is the
source; the request cannot override it.

- A local time that **does not exist** (spring-forward gap) resolves forward,
  preserving minutes.
- A local time that **occurs twice** (autumn fall-back) resolves deterministically
  via `fold=0`, so a 01:30 dose fires once, not twice.
- A bad zone name is rejected when the patient is saved (422), not at
  reminder-creation time.

`tzdata` is a pinned dependency: on Windows, `zoneinfo` resolves through the OS,
which ships no timezone database, so a wrong offset would shift a dose by hours.

#### Running the worker

The API and the worker are separate processes. The API imports the Celery app
but never connects, so it boots and serves normally with no broker running —
reminders simply stop and resume from the database when Redis returns.

```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO
celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
```

Three recurring jobs:

| Job | Cadence | Does |
| --- | --- | --- |
| `dispatch_due_reminders` | 60s | Materialise notifications for due occurrences |
| `deliver_pending_notifications` | 60s | Send what is due and still pending |
| `reconcile_scheduler` | 300s | Skip overdue occurrences, reclaim orphaned sends |

The 300s dispatch look-ahead gives four chances to materialise an occurrence
before it is due, so one missed tick does not skip a dose.

#### Batch isolation

One broken reminder — a patient with no reachable number, a deleted source row
— must not stop anyone else's reminder. Each reminder is processed inside a
SAVEPOINT, and materialising plus advancing happen in one transaction:

- A failure rolls back to the savepoint, not the batch, and increments
  `DispatchResult.failed`. A failed `flush()` otherwise leaves the PostgreSQL
  transaction aborted and every other patient's reminder in the batch would fail
  too.
- The rule is still advanced past the failed occurrence, so a permanently broken
  reminder is not retried on every tick.
- Advancing from the failed occurrence (not from now) guarantees forward
  progress; computing from now could land on the same occurrence again, because
  a look-ahead occurrence sits ahead of now.

#### Delivery providers

`NOTIFICATION_PROVIDER=console` is the default and is deliberate: it records
the attempt in the database and sends nothing, so development and tests cannot
message a patient. `whatsapp` is opt-in and needs both credentials.

- A missing credential raises a typed configuration error at **send** time, not
  at construction, so a half-configured deployment degrades one delivery instead
  of preventing the worker from booting. It never falls back to console.
- `429`/`5xx`/timeouts are transient (retry with backoff); other `4xx` is
  permanent. A `2xx` we cannot read a message id from is treated as transient,
  because an unconfirmed delivery must not be recorded as sent.
- No vendor response body ever reaches an error message. WhatsApp echoes the
  recipient's number back in most `4xx` bodies; that text would land in
  `Notification.last_error` and in API responses. The message carries a status
  code and nothing else.

#### What Phase 5 does not do

- It does not read `Medication.frequency` to build a schedule.
- It does not send late. Overdue occurrences are retired, not delivered.
- It does not notify a caregiver by default, and does not escalate on a missed
  dose.
- It does not provide opt-in/opt-out consent capture. `Patient.consent_*` does
  not exist yet, so WhatsApp to a caregiver is a Phase 6 concern.
- Its endpoints require a bearer token and a per-patient grant, as does every
  other route: see
  [Authentication and Authorization](#authentication-and-authorization).

### Phase 4 — LangGraph Grounded Answer Agent

A fixed, acyclic `StateGraph` that answers a question about **one** discharge
document using only text retrieved from that document.

#### Graph topology

```
START
  │
  ▼
validate_request ──────────────(blank query)──────────► END
  │ (valid)
  ▼
retrieve_grounded_context ─────(no chunks)───────────► END
  │ (chunks)
  ▼
generate_grounded_response
  │ (draft exists)
  ▼
validate_safety ────────────────(safe)──────────────► END
  │ (unsafe)
  ▼
safe_fallback ─────────────────────────────────────► END
```

There are **no cycles**. `tests/test_agent_graph.py` asserts the exact edge set
and walks the graph for cycles, so a future "reflect" or "retry" node would fail
the suite rather than quietly reintroduce an unbounded agent.

#### Nodes

| Node | Responsibility |
| --- | --- |
| `validate_request` | Normalise the query; reject blank input before any retrieval or provider spend |
| `retrieve_grounded_context` | Call the **existing** `RagRetrievalService`; build sources from real chunk metadata |
| `generate_grounded_response` | One call through the **existing** `LLMProvider`; returns schema-valid text and cited chunk ids only |
| `validate_safety` | Independent re-check: citations, document scoping, overreach, lexical grounding. No LLM |
| `safe_fallback` | The single terminal path for every withheld answer; emits `answer=null` and nothing clinical |

#### Module layout

```
app/agent/
├── __init__.py       public surface
├── state.py          AgentState, GroundedSource, GroundedAnswer, SafetyFlagKind, Route
├── prompts.py        the single grounded-answer prompt
├── generation.py     provider call + citation resolution
├── safety.py         AgentSafetyValidator, SafetyAssessment
├── graph.py          nodes, routers, build_agent_graph
└── service.py        AgentService (public entry point), AgentResult
app/schemas/agent.py  GroundedAnswerRequest / GroundedAnswerResponse
app/api/routes/agent.py  POST /api/v1/agent/query
```

#### How source grounding is enforced structurally

The model is asked for `cited_chunk_ids` — opaque ids it was shown — and
**nothing else about provenance**:

```
model output                real retrieved chunk
------------                --------------------
cited_chunk_ids  ────────►  GroundedSource(chunk_id, source_page, score)
```

`source_page` is copied from the Phase 3 chunk and is never accepted from the
model. Two consequences, both intentional:

- a **fabricated chunk id** is detected (it resolves to nothing) and the answer
  is withheld;
- a **fabricated page number** is not merely discouraged — it is unrepresentable.

The model can therefore only *select among* provenance the system already
established. It can never assert it.

#### Phase 4 error semantics

| Condition | Status | Why |
| --- | --- | --- |
| Answer withheld (no match, unsupported, overreach, fabricated citation) | **200** | A designed outcome: `answer=null`, `needs_review=true`, `safety_flags` populated |
| Document not owned / not found | 404 | Same body as Phase 3, so document IDs cannot be enumerated |
| Provider not configured | 503 | Actionable; identical to Phase 2 |
| Provider timeout / rate limit / auth | 504 / 429 / 502 | Identical to Phase 2, so a client can retry |
| Vector store or embedding unavailable | 503 | Identical to Phase 3 |
| Fingerprint mismatch | 409 | Inherited from Phase 3 |
| Blank or invalid request | 422 | Rejected at the edge |
| Unforeseen internal fault | 200 | Contained, fails closed, nothing asserted — no stack trace to the client |

Domain errors **propagate** rather than becoming `answer=null`. Reporting a
provider outage as "this document does not cover your question" would be
actively misleading, and disguising an authorization failure as a refusal would
hide an isolation bug.

#### Configuration

All optional; the agent is safe with nothing configured.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT_MAX_TOP_K` | `8` | Ceiling on a client-supplied `top_k` |
| `AGENT_MAX_PROMPT_CHARS` | `12000` | Cap on source text per prompt |
| `AGENT_MIN_OVERLAP_RATIO` | `0.30` | Required lexical overlap with cited sources |
| `AGENT_LOG_GRAPH_TOPOLOGY` | `false` | Log node/edge names at startup |

There is **no agent-specific API key**: the agent reuses the Phase 2 provider
(`LLM_PROVIDER` plus that provider's key).

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

### Phase 3 - Grounded RAG Retrieval

Indexes the text Phase 2 already extracted into ChromaDB and returns
**verbatim source passages** with the page each came from. There is no LLM
call anywhere in `app/rag/`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/rag/retrieve` | Search one discharge document for grounded excerpts |
| `POST` | `/api/v1/rag/documents/{id}/index` | Chunk, embed, and store one document |

`retrieve` requires `patient_id`, `document_id`, and `query`. The
`document_id` is **required and never inferred**: there is no
"search everything" mode, so a missing authorisation check can never widen
the blast radius. `top_k` is optional and clamped to `RAG_MAX_TOP_K`.

Indexing is **explicit**. Uploading a document does not index it, so the
Phase 2 pipeline is unchanged and a document can be re-indexed after chunking
settings change without re-uploading the file.

Before first use, fetch the embedding weights once (see
[Embedding providers](#embedding-providers)):

```powershell
python -m app.rag.embeddings.fetch_model
```

Until then, the retrieval endpoints return an actionable
`EmbeddingNotConfiguredError` rather than silently degrading.

#### Phase 3 error semantics
| Status | Meaning |
| --- | --- |
| `404` | Document not found **or** not owned by this patient (identical body for both) |
| `422` | Blank query, malformed ids, or a document that cannot be indexed |
| `502` | The embedding provider failed to produce a vector |
| `503` | The Chroma index or the embedding provider is unavailable |

#### Module layout
```
app/rag/
  chunking.py        page-scoped, deterministic chunker
  vector_store.py    Chroma wrapper; owns the tenancy filter
  indexing.py        document -> chunks -> vectors
  retrieval.py       query -> grounded chunks
  embeddings/
    base.py          EmbeddingProvider ABC + relevance floor
    semantic.py      local ONNX sentence embedder (default)
    hashing.py       model-free lexical fallback
    fetch_model.py   one-time operator CLI to fetch the weights
    factory.py       provider selection
```

`vector_store.tenancy_filter()` builds the `where` clause in one place, and
`patient_id` and `document_id` are **required parameters** of the store's
`query` and `delete_document`. There is no code path that reads or deletes
the collection unfiltered.

#### Embedding providers

The default is `semantic`: `BAAI/bge-small-en-v1.5` (33M parameters, 384
dimensions, ~127 MB of ONNX weights) executed **locally on CPU** by ONNX
Runtime. It matches meaning, so "blood thinner" finds a document that only
says "warfarin … to thin your blood".

Why this model and this runtime:

- **Local, so no PHI leaves the host and no API key exists.** Inference makes
  no network call at all.
- **ONNX Runtime, not torch.** torch would add a ~2.5 GB install for a model
  this small, and ONNX Runtime is *already* present as a chromadb dependency,
  so real semantic retrieval costs no new heavyweight runtime.
- **CPU only, deliberately.** A GPU execution provider would make a vector
  depend on the hardware that produced it, breaking re-index reproducibility.
- **BGE needs an asymmetric query instruction.** The prefix
  `Represent this sentence for searching relevant passages: ` is applied to
  the query side only; adding it to documents degrades their vectors.

Fetch the weights once, as an explicit deployment step:

```powershell
python -m app.rag.embeddings.fetch_model
```

The application never downloads them at runtime. Set
`RAG_SEMANTIC_ALLOW_DOWNLOAD=false` on an air-gapped host; that flag only
changes which error is returned when the files are missing, it never
triggers a fetch. The fetched directory records the repo and revision it came
from, so re-running the command against a *different* revision refuses rather
than silently replacing weights that no longer match the index.

`hashing` remains available as a model-free lexical fallback for air-gapped
installs. It matches shared **words** only, so it will not connect "water
tablet" to "paracetamol".

#### Index fingerprint: why a provider switch is caught

ChromaDB validates vector **width** but has no concept of which model produced
the numbers. `semantic` and `hashing` both emit 384 dimensions, so before this
check a provider switch would blend two unrelated vector spaces in one
collection. Nothing raises, every query "succeeds", and the only symptom is
confidently wrong ranking — a passage about the wrong drug outranking the one
that answers the question.

Each provider therefore describes itself, and the description is written into
the collection's metadata on creation:

| Metadata key | Example |
| --- | --- |
| `careloop_embedding_fingerprint_version` | `1` |
| `careloop_embedding_provider` | `semantic` |
| `careloop_embedding_model` | `BAAI/bge-small-en-v1.5@main` |
| `careloop_embedding_dimensions` | `384` |
| `careloop_embedding_config` | `max_tokens=512;pooling=cls_norm` |
| `careloop_embedding_fingerprint` | `4be92b081d89a0a2` |

Every open re-checks it. The outcomes:

- **new or empty collection** → stamped automatically; nothing to be
  inconsistent with
- **matching fingerprint** → normal operation
- **mismatched field** → `409`, naming the field that disagrees and telling the
  operator to delete the collection and re-index
- **non-empty collection with no fingerprint** (an index from before this
  existed) → also refused. Its vectors' origin is unknown, and adopting them
  would be exactly the silent corruption this prevents.

`rag_semantic_query_instruction` and `rag_semantic_model_dir` are deliberately
**excluded** from the fingerprint: the first applies to queries only, and the
second is a location rather than an identity, so neither invalidates a stored
document vector. Including them would force pointless full re-indexes.

#### Configuration
See `.env.example`. The values that matter most:

| Setting | Default | Notes |
| --- | --- | --- |
| `CHROMA_PERSIST_DIRECTORY` | `chroma_data` | Git-ignored; holds verbatim patient text |
| `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` | `1000` / `150` | Overlap must be at most half the size |
| `RAG_EMBEDDING_PROVIDER` | `semantic` | `hashing` for a model-free lexical install |
| `RAG_SEMANTIC_MODEL_DIR` | `models/embeddings/bge-small-en-v1.5` | Git-ignored local weights |
| `RAG_MIN_SCORE` | *unset* | Leave unset to use the provider's calibrated floor |

`RAG_MIN_SCORE` is optional and normally should stay unset, because the floor
is a property of the **model**: a dense encoder compresses cosine similarity
into roughly 0.45–0.75 and never approaches 0 or 1, so its floor is `0.60`,
while the lexical provider spans a far wider range and uses `0.10`. Applying
one model's floor to the other either floods results with off-topic chunks or
discards every genuine match. Set a number only when tuning against your own
corpus.

The floor exists because a vector store **always** returns its N
nearest neighbours, whether or not they mean anything. Without a floor, a
query about a drug the document never mentions comes back with its
least-unrelated passage attached �?" which a caller could easily read as an
answer. Anything below the floor is dropped and the response reports zero
matches.

---

## Authentication and Authorization

Every endpoint except the two health probes requires a signed bearer token, and
every patient-scoped route additionally requires a **grant** naming the patient
the request is about. Before this phase the routes scoped rows correctly but
had no identity to scope *for*, so anyone who could reach the API could read or
write any patient's record.

### The model

Two tables, and deliberately not more:

| Table | Meaning |
|---|---|
| `app_users` | the authenticated principal. A login, **not** a role. |
| `patient_access` | the grant: this user may act on this patient. |

There is **no global patient role** - no "nurse" or "admin" that implies access
to everyone. A grant names one patient. The `relationship` column
(`self` | `caregiver` | `care_team`) is recorded for audit and for the CLI; all
three grant exactly the same access, and none of them is a write role.

`relationship` is a `String` with a `CHECK` constraint rather than a PostgreSQL
enum, so it still holds for a direct `INSERT` from psql or a future service.
`ux_patient_access_user_patient_active` is a **partial** unique index on
`(user_id, patient_id) WHERE revoked_at IS NULL`, which both enforces one live
grant per pair and serves the per-request authorization lookup.

A revoked grant is retained rather than deleted, so `patient_access` is an
access *history*. Re-granting writes a new row; it never revives the old one.

### Operator rights

`app_users.system_access` is the single global privilege. It guards exactly two
endpoints - `POST /notifications/dispatch` and `POST /notifications/retry` -
which walk every patient's rows. It is **off by default**, no patient grant
substitutes for it, and it is never required by a patient-scoped route. An
operator can still use the ordinary clinical endpoints; it is a privilege, not
a different class of user.

### Getting a token

There is no `POST /login` (see Known Limitations). An operator provisions
access with the CLI, which is the only way to create an account or move a grant:

```powershell
# Create an account. Omit --password to be prompted, or pipe one line in.
python -m app.cli.manage_access create-user --email nora@example.com

# Grant it access to one patient.
python -m app.cli.manage_access grant --email nora@example.com `
    --patient-id <uuid> --relationship caregiver

# Mint a token for a call.
python -m app.cli.manage_access mint-token --email nora@example.com
```

Then:

```powershell
curl.exe -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/patients
```

**Account and grant management is deliberately not exposed over HTTP.** A route
that could mint a grant would let any authenticated caller escalate to reading
every patient in the system.
`tests/test_security_routes.py::test_grants_can_only_be_created_by_the_patient_creation_route`
fails if one is ever added.

The one self-service grant is `POST /patients`, which grants the creator `self`
access to the patient it just created - otherwise a created patient would be
unreadable by its own creator. It is safe because the granted id is the one the
route just minted, never one the caller supplied.

### Error semantics

The two distinctions below are the whole disclosure story, and they run in
opposite directions on purpose.

| Situation | Status | Why |
|---|---|---|
| No / malformed / expired / invalid / deactivated token | `401` | not authenticated |
| Authenticated, but no grant for a `patient_id` the request names | `403` | authenticated, not permitted |
| Authenticated, but a `*_id` resource is not theirs | `404` | must not confirm it exists |
| Malformed UUID in a path | `422` | matches FastAPI's own validation |

A patient-keyed request (`/patients/{patient_id}/...`) is refused `403` without
the grant lookup ever touching the `patients` table, so a `403` cannot be
distinguished from a `403` for an id that does not exist. A resource-keyed
request (`/medications/{medication_id}`) has to read the row to learn its owner,
so it answers `404` - byte-identical to the response for a nonexistent id.

Every `401` carries `WWW-Authenticate: Bearer realm="careloop", error="invalid_token"`
and the same body for all nine failure modes (absent, garbage, wrong key,
malformed sub, unknown user, expired, no expiry, inactive user, truncated
signature). The specific reason is logged and never returned.
`tests/test_security_authentication.py` asserts the bodies are *equal*, not
merely that each is a `401`, because "each is 401" is not the property.

### Public surface

Exactly two routes are unauthenticated:

- `GET /api/v1/health`
- `GET /api/v1/health/ready`

They must stay free of patient data; `test_public_router_touches_no_patient_data`
enforces that by checking the health handlers do not reach a model module.

**In production the interactive API docs are disabled** (`/docs`, `/redoc`,
`/openapi.json` all return 404), so the route table is not published. They
remain available in development.

### Configuration

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | *required* | >= 32 chars. `openssl rand -hex 32`. Rotating it logs everyone out. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | must be 1-1440; the app refuses to start outside that. |

In production the app also **refuses to start** if `SECRET_KEY` contains a known
placeholder (`changeme`, `secret`, `password`, `dev`, `test`, ...). That check is
a substring match, so `please-changeme-please` is caught, and `:` is treated as
a boundary so a `DATABASE_URL` pasted into the field is caught too. In
development the check is skipped, so a local `.env` is not blocked from a
convenient value.

### Deploying this

- Set a real `SECRET_KEY` per environment. A shared key across staging and
  production means a staging compromise forges production tokens.
- `/docs` is disabled in production, but that is not access control - it only
  stops the route table being read. The API is still reachable.
- Do not treat "the API requires a token" as "the API is safe to expose." There
  is no login, no refresh, no rate limiting, and no denylist on this phase.
- Terminate TLS in front of the API. A bearer token is a credential, and one
  in cleartext over a network is a disclosed credential.

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
  * **Redis** (Phase 5, required to *deliver* reminders): the Celery broker.
    Not required to run the API or the test suite — reminders can be created,
    listed, and inspected with no broker running, and delivery resumes from the
    database once Redis is back. On Windows:
    ```powershell
    winget install Redis.Redis
    # or with Docker:
    docker run -d -p 6379:6379 redis:7-alpine
    ```
  * **WhatsApp Cloud API credentials** (Phase 5, optional): only if
    `NOTIFICATION_PROVIDER=whatsapp`. The default `console` provider needs
    nothing and sends nothing.

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

> The docs URLs are development-only; in production all three return 404.

### Before running any example below

Every endpoint in the examples that follow except the two health probes needs a
bearer token, and the `notifications/dispatch` and `notifications/retry` examples
additionally need an **operator** token. Unauthenticated calls return `401`.

Mint the tokens once, then set `$headers` and pass `-Headers $headers`:

```powershell
# A caregiver token, and an operator token for the whole-system endpoints.
$caregiver = python -m app.cli.manage_access mint-token --email nora@example.com
$operator  = python -m app.cli.manage_access mint-token --email ops@example.com

$headers = @{ Authorization = "Bearer $caregiver" }
$opHeaders = @{ Authorization = "Bearer $operator" }
```

The examples below are written as they were before authentication and omit
`-Headers $headers` for brevity. Add it to each call; `-Headers $opHeaders` for
the two dispatch/retry calls.

### Trying the reminder endpoints (Phase 5)
```powershell
# 1. A medication reminder needs EXPLICIT dose times. `frequency` is free text
#    and is never parsed, so there is no schedule field to send.
$reminders = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/reminders/medication" `
  -ContentType "application/json" `
  -Body '{"medication_id":"<uuid>","times":["08:00","20:00"]}'

# 2. Run a due scan by hand instead of waiting for the beat tick. Idempotent.
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/notifications/dispatch" `
  -ContentType "application/json" -Body '{}'

# 3. What the patient was actually told.
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/notifications"
```

Then start the worker and beat in two more terminals to actually deliver:
```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO
celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
```

### Trying the check-in endpoints (Phase 6)
```powershell
# 0. The pathway is INERT by default. With no configuration a submission is
#    recorded and evaluated but creates no escalation and sends nothing. To see
#    an escalation in `.env`, set BOTH (setting one without the other is a
#    startup error):
#      CHECKIN_ESCALATION_ENABLED=true
#      CHECKIN_NOTIFY_CAREGIVER=true
#    NOTIFY also needs Patient.caregiver_contact; without it the escalation is
#    still created, with notification_blocked_reason recorded.

# 1. What to ask, and this patient's own warning-symptom ids. The ids come from
#    the question set; a client never invents them.
$questions = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/checkins/questions"
$questions.questions
$questions.available_warning_symptoms | Select-Object id, severity

# 2. Submit today's check-in. Coded answers only - there is no free-text field.
#    Use a symptom id from step 1; `absent` and `better` do not match the
#    worsening rule.
$body = @{
  general_wellbeing  = "okay"
  condition_change   = "same"
  warning_symptoms   = @(
    @{ symptom_id = $questions.available_warning_symptoms[0].id
       change     = "worse" }
  )
} | ConvertTo-Json -Depth 5

$checkin = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/checkins/daily" `
  -ContentType "application/json" -Body $body

# 3. The verdict. `status` is completed | escalated | needs_review, and
#    `escalations` carries the rule that fired. A second submission for the same
#    date returns 409, including under a genuine race.
$checkin.status
$checkin.needs_review
$checkin.patient_message        # a fixed sentence, chosen by configured workflow
$checkin.escalations | Select-Object rule_code, severity, status

# 4. The audit trail, newest first (a plain array). `status` filters the
#    lifecycle: pending | notified | acknowledged | resolved | cancelled.
$esc = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/escalations?status=pending"

# 5. A human works the queue. NOTE the order: `acknowledge` requires `notified`,
#    because acknowledging means "a human has seen that this was communicated".
#    Until the notice actually delivers, the only human action on a `pending`
#    escalation is `cancel`. So either run the worker first (delivery moves it
#    pending -> notified), or cancel it here:
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/escalations/$($esc[0].id)/cancel"

#    ...or, once delivered and therefore `notified`:
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/escalations/$($esc[0].id)/acknowledge"
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/patients/$($patient.id)/escalations/$($esc[0].id)/resolve" `
  -ContentType "application/json" -Body '{"note":"Reviewed; patient contacted."}'

#    Every transition is one-way; `resolved` and `cancelled` are terminal, and
#    going backwards returns 409 rather than rewriting the audit trail.

# 6. Recovery is automatic, not an endpoint. Submission materialises the
#    caregiver notification inline, so the recovery task only has to cover the
#    gaps (a failed outbox write, or enabling the pathway after the fact). It
#    runs on the beat every minute as `careloop.notify_pending_escalations`;
#    there is deliberately no HTTP trigger, because a second way in to
#    escalation notifications is a second place for a duplicate to be born.
#    Force it by hand against a running worker:
#      celery -A app.workers.celery_app.celery_app call careloop.notify_pending_escalations
#
#    Note that POST /notifications/dispatch is the Phase 5 due-REMINDER scan
#    only; it does not run escalation recovery.
```

An escalation is a **flag for a human, not a triage decision**. Nothing in this
phase pages anyone, and a match is not a diagnosis — see
[Known Limitations](#known-limitations).

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

### Asking a grounded question (Phase 4)

```powershell
# 1. Index the document (Phase 4 never indexes implicitly).
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/rag/documents/$documentId/index" `
  -ContentType "application/json" `
  -Body "{`"patient_id`":`"$($patient.id)`"}"

# 2. Ask a question about it.
$answer = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/agent/query" `
  -ContentType "application/json" `
  -Body "{`"patient_id`":`"$($patient.id)`",`"discharge_document_id`":`"$documentId`",`"query`":`"What medication was prescribed for pain relief?`"}"

$answer.supported      # true only if grounded AND safety-validated
$answer.answer         # null when nothing could be grounded
$answer.needs_review   # true whenever answer is null
$answer.safety_flags   # why an answer was withheld
$answer.sources        # chunk_id + source_page + score, from real metadata
```

Read the three fields together. `supported = $true` means the answer is anchored
to the cited passages and passed independent validation. `answer = $null` with
`needs_review = $true` is a **designed safe outcome**, not a failure. Check
`safety_flags` to see whether the document simply does not cover the question
(`no_retrieval_match`) or whether the model's answer was rejected
(`fabricated_source`, `medical_overreach`, `grounding_not_established`).

The answer reports what the document says. It is not a diagnosis, not a
treatment recommendation, and not a substitute for the care team.

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
- `app/core/`: Configuration (`config.py`), database engine (`database.py`), logging (`logging.py`), domain exceptions (`exceptions.py`), secret redaction (`redaction.py`), password hashing and JWT signing/verification (`security.py`), and the Phase 6 rule vocabulary (`escalation_codes.py`, dependency-free so settings can validate rule codes without importing the model layer).
- `app/models/`: SQLAlchemy 2.x declarative models (`patient`, `medication`, `appointment`, `warning_symptom`, `checkin`, `escalation`, `adherence_log`, `discharge_document`, `extraction_run`, `notification`, `reminder`, and `user` — the `AppUser` principal and the `PatientAccess` grant).
- `app/schemas/`: Pydantic v2 schemas (`Create`, `Update`, `Response`, plus the Phase 2 extraction contract and the Phase 6 check-in/escalation contracts).
- `app/repositories/`: Direct database queries, isolating SQLAlchemy session interactions.
- `app/api/auth_deps.py`: the authentication and authorization dependencies every route composes. Kept out of `deps.py` so the two concerns can be read separately.
- `app/cli/manage_access.py`: operator-only account and grant management. Deliberately not exposed over HTTP.
- `app/services/`: Application workflows, ownership verification, cascading checks, transactions. Phase 2 adds `storage`, `text_processing`, `ocr`, `document_processing`, `extraction`, `safety`, and `discharge_document`. Phase 5 adds `reminder` and `notification`. Phase 6 adds `checkin_questions`, `daily_checkin`, `red_flag_rules` (the pure evaluator), `escalation`, and `checkin`. Authorization adds `access_control` (the single place a grant decision is made).
- `app/llm/`: `LLMProvider` abstraction with `groq_provider` and `gemini_provider` adapters and a `factory`.
- `app/rag/`: Phase 3 retrieval. `chunking` (page-scoped, deterministic), `vector_store` (ChromaDB, owns the tenancy filter), `indexing` and `retrieval` (orchestration), and `embeddings/` (`EmbeddingProvider` abstraction with a local ONNX `semantic` provider and a `hashing` fallback). **Contains no LLM call.**
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
- `discharge_documents` - one row per upload: server-side filename, SHA-256,
  processing/OCR/extraction status, page count, extracted text.
- `extraction_runs` - one row per extraction attempt: provider, model,
  status, character count, and the sanitised error summary.

### Phase 3 pipeline
```text
index
   chunking.split_pages      (--- PAGE n --- markers, never spans a page)
   chunking.split_page       (paragraph > sentence > word, guaranteed stride)
   embeddings provider       (semantic: local ONNX, CPU; or hashing)
   vector_store.upsert       (delete_document first, then batch write)

retrieve
   ownership check           (PostgreSQL, before any vector-store call)
   embedding provider        (query -> unit vector)
   vector_store.query        (where: patient_id AND discharge_document_id)
   drop chunks < the provider's calibrated floor
   return verbatim text + page + score
```

No database table is added in Phase 3. The index lives in ChromaDB, and
`extraction_run_id` on each chunk ties a passage back to the Phase 2 audit
row that produced the text.

`discharge_documents` uses `ON DELETE RESTRICT` for the patient and
`extraction_runs` uses `ON DELETE CASCADE` for the document.

### Phase 6 pipeline
```text
submit daily check-in
   → checkin_questions            (versioned question set + this patient's
                                   warning symptoms, loaded WITH a patient filter)
   → build_reports                (drop ids not in that patient's set →
                                   unrecognised_warning_symptom; a cross-patient
                                   id is indistinguishable from a nonexistent one)
   → red_flag_rules.RuleSet       (pure function: stored facts + config → matches
                                   + review codes. No clock, no model, no I/O.)
   → persist CheckIn             (status, responses JSON, needs_review)
   → persist Escalation rows      (one per matched rule; UNIQUE(checkin_id, rule_code))
   → notification materialisation (Phase 5 outbox, caregiver_contact ONLY,
                                   idempotency key from escalation + channel)
   → single commit
```

The service owns the single commit, so a check-in row, its escalations, and
their outbox notifications are all-or-nothing. An escalation can never exist
without the check-in that justifies it.

### Phase 6 tables
- `checkins` - gains `responses` (JSON), `timezone`, `status`, `needs_review`,
  `review_reason`, and `completed_at`. `response_text` becomes nullable so a
  structured row can exist without free text; `UNIQUE (patient_id, date)` is
  shared by both shapes.
- `escalations` - one row per matched rule per check-in: `rule_code`,
  `rule_version`, `category`, `workflow`, `status`, `severity`,
  `warning_symptom_id`, `reason_code`, and the lifecycle timestamps
  (`notified_at`, `acknowledged_at`, `resolved_at`, `resolution_note`) plus
  `notification_blocked_reason`. `UNIQUE (checkin_id, rule_code)` makes
  re-evaluation idempotent.
- `reminders` - gains the `checkin` reminder type, with
  `UNIQUE (patient_id, reminder_type, local_time, timezone)` so a patient
  cannot silently end up with two daily prompts.
- `notifications` - gains the `checkin_prompt` and `escalation_notice` types,
  a nullable `escalation_id`, and
  `UNIQUE (idempotency_key)`, which is what makes a retried recovery run safe.

`WarningSymptom` rows are the only clinical input the rules read, and they are
`ON DELETE RESTRICT` from an escalation, so an audit row can never outlive the
stored fact it was derived from.

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

Phase 3 adds retrieval, which introduces a different risk: a citation that
looks authoritative but points at the wrong text. Three properties are
enforced in code, independent of configuration:

- **Chunks never span a page.** `source_page` is therefore exactly the page
  the text came from. It is never averaged, approximated, or defaulted.
- **An unknown page is `null`, never guessed.** Text that appears before the
  first page marker is reported as un-attributed rather than being filed
  under page 1.
- **Source text is verbatim.** Only the cut points are chosen; no word is
  rewritten, reordered, summarised, or removed. A retrieved passage can be
  found character-for-character in the stored `extracted_text`.

And one deliberate omission: the Phase 3 API has **no** `answer`, `summary`,
`interpretation`, `recommendation`, or `diagnosis` field. `app/rag/` contains
no LLM call. A test asserts those field names stay absent, so adding
answer-shaped output later is a deliberate act rather than an accident.

Phase 4 does add a generated answer, which is the first phase to cross that
line. The properties below are enforced in code, independent of configuration
and independent of the model:

- **The model cannot assert provenance.** It is asked only for `cited_chunk_ids`
  and never for a page number. Sources are resolved from the chunks retrieval
  actually returned, so a fabricated page number is unrepresentable and a
  fabricated chunk id is detected and withheld.
- **The model is not trusted.** `agent/safety.py` assumes the prompt was
  ignored and re-checks the output, reusing Phase 2's `has_overreach()` rather
  than a parallel rule set. Diagnosis, dosage change, medication change, and
  emergency phrasing are all flagged.
- **Flagged answers are withheld, never rewritten.** Correcting clinical wording
  is itself a medical judgement this system does not make.
- **Any doubt fails closed.** `answer=null`, `supported=false`,
  `needs_review=true`, and a machine-readable `safety_flags` reason. A validator
  on both `AgentState` and the response schema makes a confident-looking empty
  answer impossible to emit.
- **The graph is acyclic and stateless.** No loops, no memory, no
  conversation history, and no second LLM call to "double-check" the first.
- **Tenancy is inherited, not reimplemented.** The agent has no Chroma call and
  no second filtering path; `patient_id` + `discharge_document_id` scoping and
  the embedding fingerprint check come from the Phase 3 service unchanged.

**What the grounding check does not do.** It is a deterministic lexical guard:
it confirms the answer reuses the vocabulary of the passages it cites. It is
**not** semantic entailment, **not** medical verification, and **not** a proof
that hallucination is impossible. A fluent sentence reusing a cited passage's
vocabulary could still pass. This is why `needs_review` is part of the public
contract and why any output must be read by a clinician before it reaches a
patient. The system is built to fail toward silence, not toward a confident
wrong answer.

Phase 6 reads a patient's own reported symptoms and can cause a message about
them to be sent, so its properties are enforced in code, independent of
configuration and independent of any model:

- **No symptom is ever inferred.** A rule can only fire on a
  `WarningSymptom` row belonging to that patient. `build_reports()` receives
  ids pre-filtered by patient, so an id belonging to someone else is
  indistinguishable from one that does not exist — there is no code path that
  hands an unverified id to a rule.
- **Nothing is generated.** `patient_message` is a lookup from a fixed set of
  sentences chosen by the *configured* workflow, not text written per patient.
  The caregiver body is built from audit fields only: check-in date, rule code,
  rule version, and the severity label a human already wrote on the symptom
  row. It deliberately excludes the patient's answers, the symptom description
  (document-derived text, which may not belong in front of a non-care-team
  recipient), and any clinical instruction.
- **The system derives no severity.** It compares a stored label against a
  configured floor. It never ranks, combines, or synthesises a severity of its
  own, and a label it cannot rank is skipped as `unknown_symptom_severity`
  rather than defaulted to "low", which would let an unreadable symptom pass
  every threshold.
- **Unevaluable is never rounded to safe.** Distress with no matched symptom is
  `unmapped_distress` → `needs_review`. It is not escalated (there is no
  criterion to match) and it is not cleared (the patient may be reporting
  something real). A human decides.
- **Configuration cannot widen a clinical rule.** The severity floor can only
  be raised, and it is explicitly exempt from the worsening rule so a knob
  cannot silence a clinical signal. An unknown rule code is a startup error, so
  a typo cannot disable the pathway.
- **The pathway is inert until configured.** Both switches default off, so a
  fresh deployment records and evaluates but acts on nothing, and an escalation
  notice is never sent to a patient by mistake.
- **A caregiver notice never falls back to the patient.** Missing
  `caregiver_contact` parks the escalation with a recorded reason instead of
  improvising a recipient. Sending a clinical alert to the patient would
  disclose that the system has flagged them, before any human decided that was
  right.
- **Human decisions are terminal.** Escalation transitions are one-way, and a
  late notification retry cannot overwrite a `resolved` or `cancelled`
  status — only `notified_at` moves.

---

## Known Limitations
  - **There is no login endpoint, and no refresh token.** A principal is an
    `AppUser` row, but there is no `POST /login` and no password-recovery flow:
    accounts are created by the operator CLI
    (`python -m app.cli.manage_access create-user`) and tokens are minted with
    `manage_access mint-token`. That is a deliberate deferral, not an oversight
    - it keeps password handling and session strategy out of this change - but
    it means there is no self-service path, and a human is required to onboard
    anyone. Tokens last 60 minutes by default with no refresh, so a real client
    needs a token-minting service in front of it.
  - **Revocation is immediate, but tokens are not re-checked for expiry.**
    Deactivating an account or revoking a grant takes effect on the very next
    request regardless of the token's remaining life. There is no denylist: a
    token that was valid stays valid until it expires, so an operator who
    suspects a stolen token must wait out `ACCESS_TOKEN_EXPIRE_MINUTES` or
    rotate `SECRET_KEY`, which logs everyone out.
  - **There is no caregiver consent capture.** `Patient` has no `consent_*`
    field, so a caregiver is used as a recipient only if
    `caregiver_contact` is already populated, with no record that the patient
    agreed. Consent needs to be modelled before caregiver delivery is safe.
    `CHECKIN_NOTIFY_CAREGIVER` is an operator setting, not a consent record.
  - **An escalation is a flag, not a triage.** A match records a review
    obligation for a human; nothing in this phase routes it to anyone, pages
    anyone, or measures how long it sat unanswered. The lifecycle timestamps
    (`acknowledged_at`, `resolved_at`) are set by the service as a human works
    the queue, but a deployment still has to put a human and a queue behind
    them.
  - **Rules are anchored on Phase 2 warning symptoms.** A patient with no
    documented warning symptoms can escalate nothing, however unwell they
    report themselves — the answer becomes `unmapped_distress` and waits for a
    human. This is the deliberate trade for refusing to infer a symptom, but
    it means the pathway is only as good as the discharge documentation it is
    fed.
  - **The severity floor is a deployment-wide constant.** It cannot vary per
    patient or per condition, so a population of very frail patients and a
    population of post-appendectomy patients cannot both be tuned correctly
    with one value.
  - **Do not use `alembic revision --autogenerate` for Phase 6 tables.**
    `escalations` and `notifications` reference each other (the escalation points
    at the notification it produced; the notification points back at the
    escalation it was made for), and SQLAlchemy cannot sort that cycle, so
    autogenerate warns that *"Foreign key constraints involving these tables
    will not be considered"* and quietly omits them. A generated migration would
    therefore apply cleanly and still leave the schema wrong. These two tables
    are hand-written in
    `alembic/versions/20260928_0915_e6f1a2b4c7d9_create_phase_6_checkin_and_.py`
    for that reason — `alembic check` (not autogenerate) is the right drift
    gate, and it passes.
  - **No missed-dose escalation.** An occurrence that passes un-sent is retired
    and counted, and nothing alerts anyone — Phase 6 escalates on reported
    symptoms, not on medication adherence. A missed dose is visible only in
    `Notification.status`.
  - **A single scheduler tick processes one batch.** If a batch cannot finish
    inside the task's 60s soft limit, the remainder is picked up on the next
    tick rather than in the same one. `SCHEDULER_BATCH_SIZE` is the knob, and
    raising it without checking the timing risks timeouts.
  - **The model weights must be fetched before first use.** The default provider
    is a real semantic model, so a fresh checkout returns an actionable
    `EmbeddingNotConfiguredError` from the retrieval endpoints until
    `python -m app.rag.embeddings.fetch_model` has been run. This is intentional:
    the application never downloads them at runtime. Set
    `RAG_EMBEDDING_PROVIDER=hashing` for a model-free install.
- **Vectors are not portable across providers or dimensions.** Changing the
  provider, the model, the revision, or `RAG_EMBEDDING_DIMENSIONS` invalidates
  every existing vector, and the collection now refuses to serve rather than
  mix them — you will get a `409` naming the field that disagrees. Delete the
  `careloop_documents` collection and re-index. Querying a mixed collection
  would return silently wrong rankings rather than an error.
- **A query sharing only a generic word with the document** ("medication" in
  a question about an unrelated drug) may still be returned with a moderate
  score rather than dropped. The score is exposed to the caller precisely so
  this case can be judged rather than hidden.
- **Hyphenation repair** joins any word split across a line, so a genuine
  compound wrapped after its hyphen (`beta-\nblocker`) becomes
  `betablocker`. Distinguishing the two needs a dictionary of known
  medication names. The trade-off is deliberate: the alternative leaves
  every wrapped medication split, which corrupts more names.
- **OCR accuracy** depends entirely on scan quality. Text-layer PDFs are
  exact; scanned pages are best-effort. Both are auditable via `source_text`.
- **Storage is local disk.** There is no object store, no virus scanning of
  uploads, and no encryption at rest beyond the filesystem's own. The Chroma
  index is included in this: `chroma_data/` holds verbatim document text and
  is git-ignored, but it is not encrypted.
- **The agent is single-hop.** It cannot chain follow-up retrievals, re-rank,
  or ask a clarifying question. Each request is one retrieval and one generation.
  This is a deliberate consequence of excluding agent loops: cost and behaviour
  stay bounded and predictable.
- **Grounding is lexical, so faithful paraphrases can be withheld.** An answer
  that uses entirely different wording from its source may fail the overlap
  guard even though it is accurate. This errs toward silence, which is the
  intended direction, but it means the agent will sometimes decline a
  questionable question. Lower `AGENT_MIN_OVERLAP_RATIO` to tolerate more
  summarising, at the cost of a weaker guard.
- **Overreach detection is phrase-based.** Novel phrasings of a dosage change or
  a diagnosis could evade the patterns. It is a backstop behind the grounding
  and citation checks, not a proof.
- **No clinical review workflow exists yet.** `needs_review=true` is a flag for
  a human; nothing in this phase routes such cases to anyone, and no Phase 4
  output should reach a patient without a clinician reading it.
- **One provider serves both phases.** The same configured LLM handles Phase 2
  extraction and Phase 4 answers, so an outage takes out both and the
  configured model is exposed to discharge-document content.

---

## Roadmap

```text
Phase 6 - Daily Check-in + Escalation   (complete)
Phase 7 - React Dashboard
Phase 8 - Security, Testing, Deployment
```

Phases 1–6 are complete and described above. Phase 6 added the daily check-in
and safe escalation layer on top of Phase 5's scheduling and notification
foundation.
