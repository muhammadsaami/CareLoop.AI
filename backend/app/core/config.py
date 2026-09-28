"""
CareLoop AI — Core Configuration
Loads all settings from environment variables via pydantic-settings.
"""
from __future__ import annotations

from functools import lru_cache
from datetime import time as dt_time
from typing import TYPE_CHECKING, List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.warning_symptom import SymptomSeverity

#: Values that must never sign a token in production.  Matched as SUBSTRINGS of
#: the lowercased key by `_is_weak_secret_key`, so "changeme" is caught whether
#: the operator wrote "changeme" or "please-changeme-please".  This is a floor,
#: not a guarantee: it cannot detect a weak-but-unrecognised key, which is why
#: the real control is generating one with `openssl rand -hex 32`.
_WEAK_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "changeme",
        "change-me",
        "change_me",
        "secret",
        "secretkey",
        "secret-key",
        "secret_key",
        "password",
        "admin",
        "test",
        "testing",
        "dev",
        "development",
        "insecure",
        "please-change-me",
        "your-secret-key",
        "your_secret_key",
        "replace-me",
        "todo",
        "none",
        "null",
    }
)

#: The `symptom_severity` labels defined in Phase 2, duplicated as plain
#: strings so `app.core.config` can validate against them without importing
#: `app.models`, which imports `app.core.database`, which imports this module.
#: A mismatch is caught by `test_phase6_config.py`, which asserts this tuple
#: still equals the enum - so the duplication cannot silently drift.
_SYMPTOM_SEVERITY_LABELS: tuple[str, ...] = (
    "low",
    "medium",
    "high",
    "critical",
)


def _is_weak_secret_key(secret_key: str) -> bool:
    """
    True when `secret_key` contains a known placeholder.

    SUBSTRING matching, deliberately.  An operator who wrote
    "please-changeme-please" or "my-secret-key-for-dev" has made the same
    mistake as one who wrote "changeme", and an exact-match check would wave
    both through while appearing - in the comment above - to catch them.  The
    check therefore looks for each placeholder ANYWHERE in the key.

    ':' is also treated as a boundary, so a `user:pass@host` DATABASE_URL pasted
    into SECRET_KEY is caught even though it does not contain a whole word.

    This is a floor, not a guarantee: it cannot detect a weak-but-unrecognised
    key, which is why the real control is generating one with `openssl rand -hex 32`.
    """
    lowered = secret_key.lower()
    if any(placeholder in lowered for placeholder in _WEAK_SECRET_KEYS):
        return True
    return any(
        part.strip().lower() in _WEAK_SECRET_KEYS
        for part in secret_key.split(":")
        if part.strip()
    )


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ─────────────────────────────────────────────────────────
    app_name: str = "CareLoop AI"
    environment: str = "development"

    # ── Database ─────────────────────────────────────────────────────────────
    # REQUIRED — supplied via environment configuration (process env or .env).
    # No code-level default exists so a missing value fails fast at startup
    # instead of silently connecting to an unintended database.
    database_url: str

    # ── Security ─────────────────────────────────────────────────────────────
    # REQUIRED — supplied via environment configuration.  Never hardcoded.
    #
    # `min_length` is not a formality: without it, an unset SECRET_KEY produces
    # a settings object that satisfies the type, and every token the system
    # mints is signed with a value that is in the public source.  Failing at
    # startup is the whole point of requiring it.
    secret_key: str = Field(..., min_length=32)

    # How long an access token stays valid.  Deliberately short: a token is a
    # bearer credential, and every one of them is a key to a patient's record,
    # so a leaked token should stop working quickly.  The cost is re-authenticating;
    # the benefit is bounding a leak.  Note the dependency re-checks the user
    # row and the grant on every request, so deactivating an account takes
    # effect immediately regardless of this value.
    access_token_expire_minutes: int = 60

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: List[str] | str = ["http://localhost:3000", "http://localhost:5173"]

    @field_validator("cors_origins")
    @classmethod
    def parse_cors_origins(cls, value: str | List[str]) -> List[str]:
        """Accept either a Python list or a comma-separated string from .env."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # ── Future-phase placeholders ───────────────────────────────────────────
    # Phase 5 consumes the WhatsApp credentials below; they stay empty by
    # default so no messaging provider is contacted unless one is selected AND
    # configured.  `redis_url` has been promoted to a real setting in the
    # Phase 5 block at the end of this class.
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""

    # ─────────────────────────────────────────────────────────────────────────────
    # Phase 2 — Discharge Summary Ingestion, OCR & Structured Extraction
    # ─────────────────────────────────────────────────────────────────────────────

    # Document storage -------------------------------------------------------
    # Relative paths are resolved against the backend/ root directory.
    # Uploaded documents are NEVER stored inside the app/ Python package.
    document_storage_path: str = "storage/discharge_documents"

    # Upload limits ----------------------------------------------------------
    max_upload_size_mb: int = 10
    max_document_pages: int = 30

    # OCR --------------------------------------------------------------------
    # Full path to the Tesseract executable.  Leave empty to use the system
    # PATH.  Never hardcode a machine-specific installation path in code.
    tesseract_cmd: str = ""
    ocr_language: str = "eng"
    # Minimum characters of embedded text on a PDF page before it is treated
    # as a text page rather than a scanned page requiring OCR.
    ocr_min_chars_per_page: int = 40
    # Render DPI used when rasterising scanned PDF pages for OCR.
    pdf_ocr_render_dpi: int = 200

    # LLM providers ----------------------------------------------------------
    # Exactly one provider is used for extraction.  Only the SELECTED
    # provider's API key is required.
    llm_provider: str = "groq"

    # Default model names live here and ONLY here, so that a model name is
    # never hardcoded in more than one place.
    # NOTE: llama-3.3-70b-versatile was decommissioned by Groq and now
    # returns 404 model_not_found.  openai/gpt-oss-120b is a current
    # general-purpose model that supports json_schema structured output.
    default_groq_model: str = "openai/gpt-oss-120b"
    default_gemini_model: str = "gemini-2.0-flash"

    groq_api_key: str = ""
    groq_model: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""

    # Seconds to wait for an LLM extraction request before failing.
    llm_timeout_seconds: int = 60

    # ── Phase 3: RAG / vector retrieval ──────────────────────────────────
    # Directory holding the ChromaDB persistent store.  Relative paths are
    # resolved against the backend/ root.  MUST be git-ignored: it contains
    # patient document text and is never a build artefact.
    chroma_persist_directory: str = "chroma_data"
    # Chroma requires 3-63 chars, alphanumeric plus '_' and '-'.
    chroma_collection_name: str = "careloop_documents"

    # Chunking.  Chunks never span a page boundary, so a chunk's
    # source_page is always exact and never inferred.
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 150
    # Chunks shorter than this are dropped as noise (table rules, stray
    # page numbers) rather than embedded.
    rag_min_chunk_chars: int = 20

    # Embedding provider.  Two ship:
    #
    #   'semantic' (default) - a real sentence-embedding model, run locally
    #       through ONNX Runtime.  Matches meaning rather than wording, so
    #       "blood thinner" finds "warfarin".  Nothing leaves the host.
    #   'hashing' - a lexical fallback with no model file at all.  Kept for
    #       air-gapped installs and as the deterministic provider the test
    #       suite runs on.
    #
    # A new provider is added by subclassing EmbeddingProvider and
    # registering it in the factory; the RAG services do not change.
    rag_embedding_provider: str = "semantic"

    # ── Semantic model ──────────────────────────────────────────────────────
    # Local ONNX model.  The default is a 33M-parameter sentence embedder
    # (384 dims, ~127 MB) chosen to be genuinely useful for retrieval without
    # a GPU or a heavyweight runtime: it runs on CPU via onnxruntime, which
    # chromadb already depends on, so no torch dependency is introduced.
    # RAG_SEMANTIC_MODEL_DIR is a LOCAL path; no request text is ever sent to
    # a third party.
    rag_semantic_model_name: str = "BAAI/bge-small-en-v1.5"
    rag_semantic_model_dir: str = "models/embeddings/bge-small-en-v1.5"
    # Model revision used to identify the weights.  Part of the vector
    # collection's fingerprint, so changing it invalidates existing vectors.
    # Pin this to an immutable commit SHA for byte-reproducible rebuilds;
    # "main" is a moving target, so re-fetching it later may legitimately
    # yield different weights and require a re-index.
    rag_semantic_model_revision: str = "main"
    # THIS FLAG NEVER TRIGGERS A DOWNLOAD. Fetching the weights is always a
    # separate, explicit operator step
    # (`python -m app.rag.embeddings.fetch_model`), so a request can never
    # cause a network call. This setting only chooses WHICH error the service
    # returns when the files are absent: true tells the operator how to fetch
    # them, false states that fetching is not permitted here. Set it to false
    # on an air-gapped host so the error does not suggest a step that cannot
    # work.
    rag_semantic_allow_download: bool = True
    # BGE models are trained with an instruction prefix on the QUERY side
    # only; applying it to documents degrades their vectors.
    rag_semantic_query_instruction: str = (
        "Represent this sentence for searching relevant passages: "
    )
    # Token budget per input.  512 is the model's trained maximum; raising it
    # is silently ignored by the ONNX graph.
    rag_semantic_max_tokens: int = 512
    # Inference threads.  0 lets onnxruntime choose.
    rag_semantic_num_threads: int = 0

    # Dimensions for the hashing provider only.  The semantic model fixes its
    # own width (384 for bge-small-en-v1.5) and this value is ignored for it,
    # so the two providers can coexist without fighting over one setting.
    rag_embedding_dimensions: int = 384
    # Buckets each token is hashed into.  A single projection lets two tokens
    # collide with opposite signs and cancel exactly, which made a document
    # containing "Warfarin" score 0.0 against the query "warfarin".  Two
    # projections mean a collision can cancel at most one contribution, so a
    # true match still ranks highly.
    rag_embedding_projections: int = 2

    # Retrieval limits.
    rag_default_top_k: int = 5
    rag_max_top_k: int = 25
    # Minimum similarity for a chunk to be returned at all.
    #
    # A vector store always returns the N nearest neighbours, whether or not
    # they mean anything: querying a document for a drug it never mentions
    # still yields its least-unrelated passage.  Returning that as a search
    # result invites a caller to read it as relevant, so anything below this
    # score is dropped and the response reports zero matches.
    #
    # LEFT UNSET (the default) means "use the selected provider's calibrated
    # floor".  The floor is a property of the model, not of this service: a
    # dense model compresses cosine similarity into roughly 0.45-0.75 and
    # never approaches 0 or 1, so the lexical provider's 0.10 would admit
    # every off-topic chunk, while the semantic provider's 0.60 would reject
    # every genuine one if applied to lexical scores.  Set a number here only
    # to tune against your own corpus.
    rag_min_score: Optional[float] = None

    # ── Phase 4 - LangGraph grounded-answer agent ───────────────────────────
    # Defaults are chosen so the agent is safe without any configuration: a
    # blank run answers nothing, which is the correct behaviour for a tool
    # that must not improvise clinical statements.
    agent_max_top_k: int = 8
    agent_max_prompt_chars: int = 12000
    agent_min_overlap_ratio: float = 0.30
    agent_log_graph_topology: bool = False

    # ── Phase 5 - Scheduling & notifications ────────────────────────────────
    # Redis backs both the Celery broker and the Celery result backend.  Left
    # empty by default so the API and its tests run with no broker at all; a
    # worker is only required to actually DELIVER reminders, never to create
    # or list them.  Redis itself holds no PHI: it carries task ids and
    # serialized arguments containing reminder ids only.
    #
    # NOTE: this replaces the Phase 1 "future-phase stub" of the same name.
    # Credentials are never hardcoded - supply the whole URL via environment.
    redis_url: str = "redis://localhost:6379/0"

    # Celery reads the broker from REDIS_URL by default.  Overridable so a
    # deployment can point the broker and the result backend at separate
    # Redis instances without touching code.
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    # Queue names are namespaced so a Phase 5 worker never competes with an
    # unrelated Celery deployment on the same broker.
    celery_queue: str = "careloop.notifications"

    @field_validator("redis_url", mode="before")
    @classmethod
    def blank_redis_url_falls_back_to_default(cls, value: object) -> object:
        """
        Treat an empty ``REDIS_URL=`` as unset rather than as an empty broker.

        An env file that carries the key with no value is an easy state to reach
        by copying a template, and it silently beats the field default: the
        worker then starts with ``broker_url=''`` and fails on its first
        publish, which reads as a Celery bug rather than a configuration one.
        Defaulting keeps a blank line harmless.
        """
        if isinstance(value, str) and not value.strip():
            return "redis://localhost:6379/0"
        return value

    # 'console' is the safe default: it records a delivery without sending
    # anything, so development and tests never contact a messaging provider.
    # 'whatsapp' is opt-in and requires its credentials in the environment.
    notification_provider: str = "console"
    notification_timeout_seconds: int = 20

    # Retry policy for transient delivery failures.  A notification is retried
    # at most `max` times with exponential backoff, then marked failed so a
    # permanently broken endpoint cannot loop forever.
    notification_max_attempts: int = 3
    notification_retry_base_seconds: int = 60
    notification_retry_max_seconds: int = 3600

    # How far ahead a due-scan looks, and how often the beat runs.  A reminder
    # is materialized once and then sent, so a short window is sufficient; the
    # value only needs to exceed the expected scheduler jitter.
    scheduler_lookahead_seconds: int = 300
    scheduler_batch_size: int = 100

    # ── Phase 6: daily check-ins and safe escalation ─────────────────────────
    # MASTER SWITCH, OFF by default.  When false, check-in submissions are
    # still recorded and still evaluated, but NO escalation is created and no
    # caregiver notification is requested.  It exists so a deployment can stop
    # the escalation pathway immediately without disabling data collection - the
    # safe direction to fail in is "record but do not act".
    #
    # The default is off because a deployment that has not configured the
    # pathway has not decided what it wants acted on.  Turning this on starts
    # creating clinical escalations the moment a rule matches, so it should be
    # a deliberate act rather than what a fresh install happens to inherit.
    checkin_escalation_enabled: bool = False

    # The local time, in the PATIENT's timezone, at which the daily check-in
    # prompt is sent.  A stored string rather than a `time` so a bad value is
    # a configuration error with a readable message instead of an import-time
    # crash in every worker.
    checkin_prompt_local_time: str = "09:00"

    # Which published red-flag rules are active.  EMPTY means "all of them",
    # which is the default: a deployment that has not thought about the rule
    # set should get the reviewed one, not a silently empty one.  An unknown
    # code is a startup error - a typo must not read as "no rules active".
    #
    # Values are the rule codes from `app.services.red_flag_rules.RULES`.
    checkin_enabled_rules: str = ""

    # A deployment may RAISE the severity floor above what a published rule
    # declares, which makes the direction-agnostic rule stricter.  It may not
    # LOWER it: configuration must not be able to create a more sensitive
    # clinical rule than the one that was reviewed and versioned.
    checkin_severity_floor: str = "low"

    # Whether a matched rule should request a caregiver notification.  OFF by
    # default, independently of the master switch above: an escalation alert
    # discloses to a third party that this patient has been flagged for review,
    # so a deployment that has not yet obtained caregiver consent must not send
    # one.  Turning this off leaves escalation events recorded and reviewable in
    # the API while sending nothing, which is the right posture for exactly that
    # deployment.
    checkin_notify_caregiver: bool = False

    # Maximum documented warning symptoms a single check-in may report on.
    # Bounded because the response is persisted as JSON: an unbounded list
    # would let one request write an arbitrarily large document into a
    # clinical record.
    checkin_max_symptom_reports: int = 20

    @field_validator("checkin_prompt_local_time")
    @classmethod
    def validate_checkin_prompt_time(cls, value: str) -> str:
        """
        Parse and normalise the daily prompt time.

        Returns the value unchanged on failure so the error surfaces from the
        cross-field validator below with context, rather than from a bare
        `ValueError` on this field.
        """
        text = (value or "").strip()
        try:
            parsed = dt_time.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                "CHECKIN_PROMPT_LOCAL_TIME must be an HH:MM local time, "
                f"for example 09:00 (got {value!r})."
            ) from exc
        if parsed.tzinfo is not None:
            raise ValueError(
                "CHECKIN_PROMPT_LOCAL_TIME must be a naive local time; the "
                "patient's timezone comes from the patient record, not from "
                "this setting."
            )
        return parsed.strftime("%H:%M")

    @field_validator("checkin_severity_floor")
    @classmethod
    def validate_checkin_severity_floor(cls, value: str) -> str:
        """Reject a severity label outside the existing Phase 2 enum."""
        name = (value or "").strip().lower()
        if name not in _SYMPTOM_SEVERITY_LABELS:
            raise ValueError(
                "CHECKIN_SEVERITY_FLOOR must be one of: "
                f"{', '.join(_SYMPTOM_SEVERITY_LABELS)}."
            )
        return name

    @field_validator("checkin_enabled_rules")
    @classmethod
    def validate_checkin_enabled_rules(cls, value: str) -> str:
        """Reject an unknown rule code rather than disabling rules silently."""
        from app.core.escalation_codes import RULE_CODES

        text = (value or "").strip()
        if not text:
            return ""
        codes = [part.strip() for part in text.split(",") if part.strip()]
        unknown = sorted(set(codes) - RULE_CODES)
        if unknown:
            raise ValueError(
                f"CHECKIN_ENABLED_RULES contains unknown rule code(s): "
                f"{unknown}. Known codes: {sorted(RULE_CODES)}."
            )
        return ",".join(codes)

    @field_validator("notification_provider")
    @classmethod
    def validate_notification_provider(cls, value: str) -> str:
        """Reject an unknown provider name at configuration time."""
        name = (value or "").strip().lower()
        if name not in {"console", "whatsapp"}:
            raise ValueError(
                "NOTIFICATION_PROVIDER must be one of: console, whatsapp."
            )
        return name

    @field_validator("chroma_collection_name")
    @classmethod
    def validate_collection_name(cls, value: str) -> str:
        """
        Enforce Chroma's collection-name rules at configuration time.

        ChromaDB raises deep inside its client for a bad name; failing here
        turns a typo into an actionable startup error instead.
        """
        name = (value or "").strip()
        if not (3 <= len(name) <= 63):
            raise ValueError(
                "CHROMA_COLLECTION_NAME must be between 3 and 63 characters."
            )
        if not name[0].isalnum() or not name[-1].isalnum():
            raise ValueError(
                "CHROMA_COLLECTION_NAME must start and end with an "
                "alphanumeric character."
            )
        if not all(ch.isalnum() or ch in "_-" for ch in name):
            raise ValueError(
                "CHROMA_COLLECTION_NAME may contain only letters, digits, "
                "underscores, and hyphens."
            )
        return name

    @field_validator("rag_chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("RAG_CHUNK_OVERLAP cannot be negative.")
        return value

    @model_validator(mode="after")
    def validate_chunking_pair(self) -> "Settings":
        """
        Reject an overlap that is not smaller than the chunk size.

        Without this the chunker would be asked to step backwards or stand
        still and could loop forever; catching it at startup is far better
        than hanging a request later.
        """
        if self.rag_chunk_size <= 0:
            raise ValueError("RAG_CHUNK_SIZE must be greater than zero.")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError(
                "RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE "
                f"(got overlap={self.rag_chunk_overlap}, "
                f"size={self.rag_chunk_size})."
            )
        if self.rag_chunk_overlap * 2 > self.rag_chunk_size:
            # The chunker advances by (size - overlap) characters per step, so
            # an overlap above 50% collapses the stride and multiplies the
            # chunk count for the same document: 100/90 turns one 24 kB page
            # into 2,383 chunks and needlessly inflates the vector store.
            raise ValueError(
                "RAG_CHUNK_OVERLAP must not exceed half of RAG_CHUNK_SIZE "
                f"(got overlap={self.rag_chunk_overlap}, "
                f"size={self.rag_chunk_size})."
            )
        if self.rag_max_top_k < 1:
            raise ValueError("RAG_MAX_TOP_K must be at least 1.")
        if not (1 <= self.rag_default_top_k <= self.rag_max_top_k):
            raise ValueError(
                "RAG_DEFAULT_TOP_K must be between 1 and RAG_MAX_TOP_K."
            )
        if self.rag_embedding_dimensions < 8:
            raise ValueError("RAG_EMBEDDING_DIMENSIONS must be at least 8.")
        if self.rag_min_score is not None and not (
            0.0 <= self.rag_min_score <= 1.0
        ):
            raise ValueError(
                "RAG_MIN_SCORE must be between 0.0 and 1.0, or left unset to "
                "use the selected embedding provider's calibrated default."
            )
        if self.rag_embedding_projections < 1:
            raise ValueError("RAG_EMBEDDING_PROJECTIONS must be at least 1.")
        if not self.rag_semantic_model_dir.strip():
            raise ValueError("RAG_SEMANTIC_MODEL_DIR must not be empty.")
        if self.rag_semantic_max_tokens < 32:
            raise ValueError("RAG_SEMANTIC_MAX_TOKENS must be at least 32.")
        if self.rag_semantic_num_threads < 0:
            raise ValueError("RAG_SEMANTIC_NUM_THREADS must be 0 or greater.")
        if self.agent_max_top_k < 1:
            raise ValueError("AGENT_MAX_TOP_K must be at least 1.")
        if self.agent_max_prompt_chars < 1000:
            raise ValueError("AGENT_MAX_PROMPT_CHARS must be at least 1000.")
        if not (0.0 <= self.agent_min_overlap_ratio <= 1.0):
            raise ValueError(
                "AGENT_MIN_OVERLAP_RATIO must be between 0.0 and 1.0."
            )

        # ── Phase 5 validation ────────────────────────────────────────────
        # A retry policy that cannot fail, or cannot succeed, is a config bug
        # rather than a runtime surprise, so it is rejected at startup.
        if self.notification_max_attempts < 1:
            raise ValueError(
                "NOTIFICATION_MAX_ATTEMPTS must be at least 1."
            )
        if self.notification_retry_base_seconds < 1:
            raise ValueError(
                "NOTIFICATION_RETRY_BASE_SECONDS must be at least 1."
            )
        if self.notification_retry_max_seconds < self.notification_retry_base_seconds:
            raise ValueError(
                "NOTIFICATION_RETRY_MAX_SECONDS must be greater than or equal "
                "to NOTIFICATION_RETRY_BASE_SECONDS."
            )
        if self.notification_timeout_seconds < 1:
            raise ValueError(
                "NOTIFICATION_TIMEOUT_SECONDS must be at least 1."
            )
        if self.scheduler_lookahead_seconds < 0:
            raise ValueError(
                "SCHEDULER_LOOKAHEAD_SECONDS must be 0 or greater."
            )
        if self.scheduler_batch_size < 1:
            raise ValueError(
                "SCHEDULER_BATCH_SIZE must be at least 1."
            )
        if not self.celery_queue.strip():
            raise ValueError("CELERY_QUEUE must not be empty.")

        # ── Security validation ─────────────────────────────────────────────
        # The signing key is the single value that makes a forged token
        # indistinguishable from a real one, so the obvious place to get it
        # wrong is the place to check.
        if self.is_production and _is_weak_secret_key(self.secret_key):
            # Checked only in production: a developer's local `.env` should not
            # be blocked from using a convenient value, but a real deployment
            # must never be.
            raise ValueError(
                "SECRET_KEY must not be a well-known placeholder in "
                "production. Generate one with: openssl rand -hex 32"
            )
        if self.access_token_expire_minutes < 1:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be at least 1.")
        if self.access_token_expire_minutes > 24 * 60:
            # Longer than a day means a leaked token is a long-lived key to a
            # clinical record. Refused rather than trusted.
            raise ValueError(
                "ACCESS_TOKEN_EXPIRE_MINUTES must not exceed 1440 (24 hours)."
            )

        # ── Phase 6 validation ─────────────────────────────────────────────
        if self.checkin_max_symptom_reports < 1:
            raise ValueError(
                "CHECKIN_MAX_SYMPTOM_REPORTS must be at least 1."
            )
        if self.checkin_max_symptom_reports > 200:
            # The responses blob is JSON in a clinical record.  A cap this
            # large is a sign something is wrong with the caller, not a
            # patient who genuinely reports 200 symptoms.
            raise ValueError(
                "CHECKIN_MAX_SYMPTOM_REPORTS must not exceed 200."
            )
        if not self.checkin_escalation_enabled and self.checkin_notify_caregiver:
            # Silently notifying while the escalation pathway is switched off
            # would be a contradiction a reader of the config could not
            # resolve.  Refuse it rather than pick a meaning for them.
            raise ValueError(
                "CHECKIN_NOTIFY_CAREGIVER cannot be enabled while "
                "CHECKIN_ESCALATION_ENABLED is false: no escalation can exist "
                "to notify about."
            )
        return self

    @property
    def checkin_prompt_time(self) -> dt_time:
        """
        The daily prompt time as a `time`, in the patient's own timezone.

        Parsed from the already-validated string.  The field validator
        guarantees the format, so a failure here would be a bug rather than a
        configuration problem - and it is surfaced as a clear error instead of
        an opaque `ValueError` from `fromisoformat`.
        """
        hour, _, minute = self.checkin_prompt_local_time.partition(":")
        return dt_time(hour=int(hour), minute=int(minute))

    @property
    def checkin_active_rule_codes(self) -> Optional[List[str]]:
        """
        The rule codes to evaluate, or None meaning "all published rules".

        None is distinct from an empty list on purpose: an operator who
        deliberately configured zero rules has made a decision, while the
        default (blank config) means "you have not decided yet, use the
        reviewed set".
        """
        if not self.checkin_enabled_rules.strip():
            return None
        return [part.strip() for part in self.checkin_enabled_rules.split(",")]

    @property
    def checkin_severity_floor_value(self) -> "SymptomSeverity":
        """
        The configured floor as the existing Phase 2 severity enum.

        Imported lazily: `app.core.config` cannot import `app.models` at module
        scope (that chain leads back here through `app.core.database`).
        """
        from app.models.warning_symptom import SymptomSeverity

        return SymptomSeverity(self.checkin_severity_floor)

    @property
    def groq_model_name(self) -> str:
        """Resolved Groq model name (config override, else single default)."""
        return self.groq_model or self.default_groq_model

    @property
    def gemini_model_name(self) -> str:
        """Resolved Gemini model name (config override, else single default)."""
        return self.gemini_model or self.default_gemini_model

    @property
    def max_upload_size_bytes(self) -> int:
        """Maximum permitted upload size in bytes."""
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment.lower() == "testing"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
