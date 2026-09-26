"""
CareLoop AI — Domain Exceptions (Phase 2)

Services raise these domain exceptions.  They are translated into HTTP
responses by the handlers registered in `app.main`.

Design rules:
- A client-safe `message` is carried on every exception.  Stack traces,
  file system paths, SQL, and provider payloads are never exposed.
- `internal_detail` exists only for server-side logging.
"""
from __future__ import annotations

from typing import Optional


class CareLoopError(Exception):
    """Base class for all CareLoop domain errors."""

    status_code: int = 500
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        internal_detail: Optional[str] = None,
    ) -> None:
        self.message = message or self.default_message
        self.internal_detail = internal_detail
        super().__init__(self.message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# ── Document ingestion ───────────────────────────────────────────────────────

class DocumentValidationError(CareLoopError):
    """Uploaded file failed extension, content-type, or signature checks."""

    status_code = 400
    default_message = "The uploaded file is not a supported document."


class UnsupportedFileTypeError(DocumentValidationError):
    status_code = 400
    default_message = (
        "Unsupported file type. Allowed types: PDF, PNG, JPG, JPEG."
    )


class FileTooLargeError(CareLoopError):
    status_code = 413
    default_message = "The uploaded file exceeds the maximum allowed size."


class PageLimitExceededError(CareLoopError):
    status_code = 400
    default_message = "The document exceeds the maximum allowed page count."


class CorruptedDocumentError(CareLoopError):
    """PDF/image could not be opened or decoded."""

    status_code = 422
    default_message = "The document could not be read. It may be corrupted."


class DocumentNotFoundError(CareLoopError):
    status_code = 404
    default_message = "Discharge document not found."


class DuplicateDocumentError(CareLoopError):
    """Identical document already exists for this patient."""

    status_code = 409
    default_message = (
        "This document has already been uploaded for this patient."
    )


class StorageError(CareLoopError):
    status_code = 500
    default_message = "Failed to store the uploaded document."


# ── Configuration ────────────────────────────────────────────────────────────

class ConfigurationError(CareLoopError):
    """A required piece of server configuration is missing or invalid."""

    status_code = 503
    default_message = "The service is not correctly configured."


class OCRUnavailableError(ConfigurationError):
    status_code = 503
    default_message = (
        "OCR is unavailable because Tesseract is not installed or not "
        "configured on this server."
    )


class OCRFailedError(CareLoopError):
    status_code = 422
    default_message = "OCR failed to process the document."


class ProviderNotConfiguredError(ConfigurationError):
    status_code = 503
    default_message = "The configured extraction provider is not available."


class MissingAPIKeyError(ConfigurationError):
    status_code = 503
    default_message = (
        "The configured extraction provider is missing its API key."
    )


# ── LLM provider failures ────────────────────────────────────────────────────

class ProviderError(CareLoopError):
    """Generic upstream provider failure."""

    status_code = 502
    default_message = "The extraction provider returned an error."


class ProviderTimeoutError(ProviderError):
    status_code = 504
    default_message = "The extraction provider timed out."


class ProviderRateLimitError(ProviderError):
    status_code = 429
    default_message = "The extraction provider rate limit was exceeded."


class ProviderAuthenticationError(ProviderError):
    status_code = 502
    default_message = "The extraction provider rejected its credentials."


class MalformedProviderOutputError(ProviderError):
    """Provider responded, but the payload was not usable JSON."""

    status_code = 502
    default_message = "The extraction provider returned malformed output."


# ── Extraction validation ────────────────────────────────────────────────────

class ExtractionValidationError(CareLoopError):
    """Extraction output failed schema or safety validation."""

    status_code = 422
    default_message = "Extraction output failed validation."


class PersistenceError(CareLoopError):
    status_code = 500
    default_message = "Failed to persist the extraction result."


class EmptyDocumentTextError(CareLoopError):
    """No text could be obtained from the document."""

    status_code = 422
    default_message = (
        "No readable text could be obtained from the document."
    )


# ── Phase 3: RAG / vector retrieval ─────────────────────────────────────


class DocumentNotIndexableError(CareLoopError):
    """
    The document exists but cannot be indexed into the vector store.

    Raised for a document that failed processing, has no extracted text, or
    whose text yields no usable chunks.  Indexing such a document would
    create ungrounded or empty retrieval results.
    """

    status_code = 422
    default_message = (
        "This document cannot be indexed because it has no usable extracted "
        "text."
    )


class VectorStoreError(CareLoopError):
    """The vector store could not be reached or returned an error."""

    status_code = 503
    default_message = (
        "The document search index is currently unavailable."
    )


class EmbeddingError(CareLoopError):
    """The embedding provider could not produce a usable vector."""

    status_code = 502
    default_message = "Text could not be converted into a search vector."


class EmbeddingNotConfiguredError(CareLoopError):
    """The configured embedding provider does not exist."""

    status_code = 503
    default_message = "The configured embedding provider is not available."


class EmbeddingFingerprintMismatchError(CareLoopError):
    """
    The stored vectors were produced by a different embedding model.

    Raised when the vector collection's recorded embedding fingerprint does
    not match the currently configured one.  This is a distinct, loud failure
    because nothing about the mixed state looks wrong locally: the collection
    opens, queries succeed, and every vector has a valid width.  The only
    symptom is quietly incorrect ranking, where a passage about one drug
    outranks a passage that actually answers the question.

    ChromaDB validates vector WIDTH but has no concept of which model produced
    the numbers, so two providers that both emit 384 dimensions are
    indistinguishable to it.  This error is the backstop.

    `409 Conflict` rather than 5xx: the request is valid, but the server's
    stored state conflicts with its configuration, and no retry will help
    until an operator re-indexes.
    """

    status_code = 409
    default_message = (
        "The search index was built with a different embedding model than the "
        "one now configured, so it cannot be queried safely."
    )


class EmptyQueryError(CareLoopError):
    """A retrieval request supplied no searchable text."""

    status_code = 422
    default_message = "Provide a non-empty query to search the document."


class RetrievalForbiddenError(CareLoopError):
    """
    The requested document does not belong to the requested patient.

    This is the tenancy guard for retrieval.  It deliberately reports the
    same 404-shaped message as a missing document so that a caller cannot
    probe for the existence of another patient's document by comparing
    status codes.
    """

    status_code = 404
    default_message = "No discharge document was found for this patient."
