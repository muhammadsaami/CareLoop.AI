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


# ── Phase 5: Scheduling & notifications ────────────────────────────────


class ReminderNotFoundError(CareLoopError):
    status_code = 404
    default_message = "Reminder not found."


class ReminderValidationError(CareLoopError):
    """
    A reminder could not be built from the supplied structured data.

    Covers an unknown timezone, a medication/appointment that does not belong
    to the patient, and a schedule that contradicts itself.
    """

    status_code = 422
    default_message = "The reminder could not be scheduled as requested."


class InvalidTimezoneError(ReminderValidationError):
    """
    The requested IANA timezone is unknown to the platform.

    422 rather than 500: the request carried a value this server cannot
    resolve, and the caller can fix it without an operator.
    """

    default_message = (
        "The supplied timezone is not a recognised IANA timezone name, "
        "for example 'America/New_York'."
    )


class NotificationNotFoundError(CareLoopError):
    status_code = 404
    default_message = "Notification not found."


class NotificationProviderError(CareLoopError):
    """Generic notification delivery failure."""

    status_code = 502
    default_message = "The notification could not be delivered."


class NotificationTransientError(NotificationProviderError):
    """
    A delivery failure that may succeed if retried.

    A timeout, a 5xx, or a rate limit.  The scheduler retries these with
    exponential backoff up to the configured attempt limit, then marks the
    notification failed so a broken endpoint cannot loop forever.

    `retryable` is the single source of truth for that decision: a provider
    sets it rather than the scheduler guessing from an exception type.
    """

    status_code = 504
    default_message = "The notification could not be delivered and will be retried."

    retryable: bool = True


class NotificationPermanentError(NotificationProviderError):
    """
    A delivery failure that retrying cannot fix.

    A rejected destination, a malformed address, or revoked credentials.
    These are failed immediately: retrying would waste the budget and, for a
    revoked credential, keep hammering a provider that will keep refusing.
    """

    status_code = 502
    default_message = "The notification could not be delivered."

    retryable: bool = False


class NotificationProviderNotConfiguredError(ConfigurationError):
    """
    The selected notification provider has no usable credentials.

    Surfaced as 503 with the same shape as Phase 2's
    `ProviderNotConfiguredError`, so a client learns the deployment is
    misconfigured rather than that the reminder was refused.
    """

    default_message = (
        "The configured notification provider is not available, so reminders "
        "cannot be delivered."
    )


class SchedulerUnavailableError(CareLoopError):
    """
    The scheduler backend (Redis / Celery) could not be reached.

    Distinct from a delivery failure: nothing was attempted, so a retry of
    the whole dispatch is meaningful.
    """

    status_code = 503
    default_message = "The reminder scheduler is currently unavailable."


# ── Phase 6: Daily check-ins & escalation ────────────────────────────────


class CheckInNotFoundError(CareLoopError):
    """
    No check-in was found for this patient.

    404 rather than 403 for the same reason as `ReminderNotFoundError`: a 403
    would confirm that the id exists, letting a caller probe for another
    patient's check-ins.
    """

    status_code = 404
    default_message = "Check-in not found."


class CheckInValidationError(CareLoopError):
    """
    The check-in answers could not be accepted as submitted.

    422: the caller supplied something the server can act on differently.
    """

    status_code = 422
    default_message = "The check-in could not be recorded as submitted."


class CheckInAlreadySubmittedError(CheckInValidationError):
    """
    This patient already has a check-in for this date.

    A 409 rather than a silent overwrite: re-submitting a day is either a
    double tap or two devices disagreeing, and quietly replacing the first
    answer would destroy the record of what the patient actually said.
    """

    status_code = 409
    default_message = (
        "A check-in has already been recorded for this date. "
        "One check-in per day is recorded."
    )


class EscalationNotFoundError(CareLoopError):
    """
    No escalation was found.

    Also the response to a cross-patient request, for the same
    do-not-confirm-existence reason as `CheckInNotFoundError`.
    """

    status_code = 404
    default_message = "Escalation not found."


class EscalationTransitionError(CareLoopError):
    """
    The requested lifecycle transition is not permitted.

    409: the escalation exists and is readable, but it is already in a state
    that makes this move meaningless - acknowledging a resolved escalation, for
    example.  Reported rather than ignored, because a caller that believes it
    acknowledged something needs to know it did not.
    """

    status_code = 409
    default_message = (
        "This escalation cannot move to that state from where it is now."
    )


class EscalationNotNotifiableError(CareLoopError):
    """
    A caregiver notification was requested but cannot be built.

    422, and deliberately NOT a silent no-op: the common cause is a patient
    record with no caregiver contact, and burying that in a log means an
    escalation sits in `pending` forever while the system looks healthy.
    """

    status_code = 422
    default_message = (
        "A caregiver notification cannot be requested for this escalation "
        "with the information currently on file."
    )


# ── Authentication & authorization ───────────────────────────────────────────


class AuthenticationError(CareLoopError):
    """
    The caller could not be identified. 401.

    Covers every way a request fails to present a usable credential: no
    Authorization header, a header that is not Bearer, a token that is
    malformed, signed with the wrong key or algorithm, expired, revoked by
    deactivation, or carrying a subject that is not a user.

    The message is FIXED.  Every subclass of this failure returns the same text
    to the client, so a caller cannot distinguish "your token expired" from
    "that signature is wrong" from "no such user" by comparing responses.  The
    specific reason goes to `internal_detail` for the server log and nowhere
    else; telling an unauthenticated caller which check failed turns the
    endpoint into an oracle for testing whether a guessed token is structurally
    valid.

    401 rather than 403: the server does not know who you are, so it cannot say
    what you are allowed to do.
    """

    status_code = 401
    default_message = "Authentication required."


class AuthorizationError(CareLoopError):
    """
    The caller is known but not permitted to do this. 403.

    Distinct from 401 on purpose: the client should re-authenticate for a 401
    and NOT for a 403, and the difference is what stops a client from looping
    on a permission it will never be granted.

    Also distinct from 404: `app.services.access_control` returns 404 when
    refusing a resource-keyed request, so that the response does not confirm
    the resource exists.  This error is used where the caller already holds the
    identifier, so existence is not disclosed either way.
    """

    status_code = 403
    default_message = (
        "You are not authorized to access this patient's records."
    )


class ResourceNotFoundError(CareLoopError):
    """
    A resource is absent, or the caller may not know that it exists. 404.

    One error for both cases, on purpose - see `app.services.access_control`.
    Answering 403 to a caller who named a resource id they do not own would
    confirm the id is real, which turns any patient-scoped endpoint into an
    enumeration oracle.  Returning the same 404 for "no such row" and "not
    yours" is what makes the identifier unguessable in practice.
    """

    status_code = 404
    default_message = "Not found."
