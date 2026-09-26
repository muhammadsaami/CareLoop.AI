"""
CareLoop AI — Core Configuration
Loads all settings from environment variables via pydantic-settings.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    secret_key: str

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: List[str] | str = ["http://localhost:3000", "http://localhost:5173"]

    @field_validator("cors_origins")
    @classmethod
    def parse_cors_origins(cls, value: str | List[str]) -> List[str]:
        """Accept either a Python list or a comma-separated string from .env."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # ── Future-phase stubs (not used in Phase 1) ─────────────────────────────
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    redis_url: str = ""

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
        return self

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
