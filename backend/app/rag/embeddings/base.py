"""
CareLoop AI - Embedding Provider Abstraction (Phase 3)

`RagIndexingService` and `RagRetrievalService` depend ONLY on the
`EmbeddingProvider` interface defined here.  Neither imports a concrete
embedding implementation, so replacing or adding a provider requires no
change to chunking, indexing, retrieval, storage, or API layers.

This mirrors the `app.llm.base.LLMProvider` pattern deliberately: the
project now has one consistent way to abstract a third-party AI dependency.

Contract:
  - `embed_documents()` and `embed_query()` return plain Python lists of
    floats, L2-normalised, of length `dimensions`.
  - The same text MUST always produce the same vector (determinism), so
    indexing is reproducible and tests are stable.
  - Every failure is translated into a domain exception from
    `app.core.exceptions`; callers never see a vendor SDK exception.
  - Provider API keys, if any, are read from configuration and are never
    logged, echoed into error messages, or returned.

HEALTHCARE SAFETY BOUNDARY
Embeddings are a numeric representation of text the clinician already
wrote.  Nothing in this module interprets, summarises, diagnoses, or
rewrites that text.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from typing import Optional, Sequence, Union

from app.core.config import Settings, get_settings
from app.core.exceptions import EmbeddingError
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Collection fingerprint keys ──────────────────────────────────────────────
# Stored in the vector collection's metadata.  The namespace prefix keeps them
# from colliding with Chroma's own `hnsw:*` keys.
#
# Every value is a scalar because ChromaDB metadata accepts only
# str/int/float/bool.  Bump the version if the set of fields or their meaning
# ever changes: an older collection then reads as a mismatch and is re-indexed
# rather than being trusted on stale assumptions.
FINGERPRINT_VERSION = 1
_KEY_VERSION = "careloop_embedding_fingerprint_version"
_KEY_PROVIDER = "careloop_embedding_provider"
_KEY_MODEL = "careloop_embedding_model"
_KEY_DIMENSIONS = "careloop_embedding_dimensions"
_KEY_CONFIG = "careloop_embedding_config"
_KEY_DIGEST = "careloop_embedding_fingerprint"

#: The keys that make up a fingerprint, in a stable order for comparison.
FINGERPRINT_FIELDS = (
    _KEY_VERSION,
    _KEY_PROVIDER,
    _KEY_MODEL,
    _KEY_DIMENSIONS,
    _KEY_CONFIG,
)

FingerprintValue = Union[str, int, float, bool]


def digest_fingerprint(parts: dict[str, FingerprintValue]) -> str:
    """
    Reduce fingerprint fields to one short, stable string.

    Sorted keys and a fixed separator mean the digest depends on the VALUES
    only, never on dict ordering, so it is stable across processes and Python
    versions.  Truncated to 16 hex characters: this is an integrity check
    against accidental mismatch, not a security control.
    """
    canonical = json.dumps(
        {key: parts[key] for key in sorted(parts)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def describe_fingerprint(fields: dict[str, FingerprintValue]) -> str:
    """
    Render fingerprint fields as readable `key=value` pairs.

    Used in error messages and logs so an operator can see exactly which field
    disagrees.  Every value originates from configuration or a model
    identifier, never from document or patient data, so this is safe to log.
    """
    return ", ".join(f"{key}={fields[key]}" for key in sorted(fields))


def fingerprint_mismatch(
    expected: dict[str, FingerprintValue], actual: dict
) -> list[str]:
    """
    Return human-readable descriptions of every disagreeing field.

    An empty list means the fingerprints match.  ChromaDB returns metadata
    with plain Python scalars, so values are compared leniently: `384` and
    `"384"` are treated as equal, because a store may have serialised an int
    as a string.
    """
    problems: list[str] = []
    for key in FINGERPRINT_FIELDS:
        if key not in actual:
            problems.append(f"{key} is missing from the collection")
            continue
        if str(actual[key]) != str(expected[key]):
            problems.append(
                f"{key} is {actual[key]!r} in the collection but "
                f"{expected[key]!r} is configured"
            )
    stored_digest = actual.get(_KEY_DIGEST)
    if stored_digest is not None and str(stored_digest) != str(
        expected[_KEY_DIGEST]
    ):
        problems.append(
            f"the stored fingerprint digest {stored_digest!r} does not match "
            f"the configured {expected[_KEY_DIGEST]!r}"
        )
    return problems

# Conservative word pattern: letters, digits, and intra-word apostrophes or
# hyphens.  Tokenisation exists only to build a bag of words for hashing; it
# never rewrites, corrects, or reorders the source text.
_WORD_RE = re.compile(r"[0-9A-Za-z]+(?:['\-][0-9A-Za-z]+)*")

# Extremely common English words carry no retrieval signal for clinical
# documents and would otherwise dominate a bag-of-words vector.
STOPWORDS = frozenset(
    """
    a an and are as at be been but by for from had has have he her his i if
    in into is it its of on or our she that the their them then there these
    they this to was we were what when which who will with you your
    """.split()
)


def tokenize(text: str) -> list[str]:
    """
    Split text into lower-cased word tokens for embedding purposes.

    This is a vectorisation helper ONLY.  The original text is always
    stored verbatim alongside the vector; this function never alters what is
    stored or returned to a caller.
    """
    if not text:
        return []
    return [t.lower() for t in _WORD_RE.findall(text)]


class EmbeddingProvider(ABC):
    """Abstract text-to-vector provider."""

    #: Stable identifier recorded in logs.
    name: str = "unknown"

    #: Similarity below which a retrieved chunk is considered off-topic.
    #:
    #: This lives on the PROVIDER, not only in settings, because the number
    #: is meaningless without the model that produced the scores.  A lexical
    #: bag-of-words model spreads similarity across roughly 0.0-0.5, while a
    #: dense sentence-transformer model compresses everything into roughly
    #: 0.45-0.75 and never approaches either extreme.  Shipping one global
    #: floor for both would either pass every irrelevant chunk through
    #: (lexical floor on dense scores) or reject every real one (dense floor
    #: on lexical scores).
    #:
    #: `Settings.rag_min_score` overrides this when an operator needs to tune
    #: against their own corpus; leaving it unset uses the calibrated value.
    default_min_score: float = 0.5

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    @property
    def relevance_floor(self) -> float:
        """
        The similarity floor that actually applies to this provider.

        A configured value always wins so an operator can tune against their
        own documents; otherwise the provider's calibrated default is used.
        """
        configured = self._settings.rag_min_score
        if configured is not None:
            return float(configured)
        return float(self.default_min_score)

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Length of every vector this provider returns."""

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the provider has everything it needs to run."""

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of document chunks."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query."""

    # ── Identity (collection fingerprint) ────────────────────────────────────
    #
    # A vector is only meaningful next to the exact model that produced it.
    # Width alone is not identity: the `semantic` and `hashing` providers both
    # emit 384 dimensions, and ChromaDB cannot tell them apart, so a provider
    # switch would otherwise mix two unrelated vector spaces in one collection
    # and return plausible-looking, wrong rankings.  Each provider therefore
    # describes itself, and the vector store records that description in the
    # collection's metadata so a mismatch is detected before any query.

    @property
    def model_id(self) -> str:
        """
        A stable, deterministic identifier for the model behind the vectors.

        Must change whenever the produced vectors would change, and must not
        change when they would not.
        """
        return self.name

    @property
    def vector_space_config(self) -> str:
        """
        Configuration that alters the produced VECTORS, as readable text.

        Only settings that change stored document vectors belong here.  A
        setting that alters only how a *query* is encoded must NOT appear, or
        the index would be needlessly invalidated even though its stored
        vectors remain valid.
        """
        return "default"

    @property
    def fingerprint(self) -> dict[str, FingerprintValue]:
        """
        The identity of this provider's vector space.

        Recorded in the collection metadata on creation and compared on every
        open.  Includes the schema version so a future change to these fields
        invalidates old collections instead of misreading them.
        """
        parts: dict[str, FingerprintValue] = {
            _KEY_VERSION: FINGERPRINT_VERSION,
            _KEY_PROVIDER: self.name,
            _KEY_MODEL: self.model_id,
            _KEY_DIMENSIONS: int(self.dimensions),
            _KEY_CONFIG: self.vector_space_config,
        }
        parts[_KEY_DIGEST] = digest_fingerprint(parts)
        return parts

    @property
    def fingerprint_digest(self) -> str:
        """Single-string form of the fingerprint, for logs and comparison."""
        return str(self.fingerprint[_KEY_DIGEST])

    # ── Shared helpers ──────────────────────────────────────────────────────

    def ensure_configured(self) -> None:
        """Raise a clear configuration error when the provider cannot run."""
        if not self.is_configured():
            raise EmbeddingError(
                f"The '{self.name}' embedding provider is not configured.",
                internal_detail=f"embedding provider not ready: {self.name}",
            )

    @staticmethod
    def _validate(
        vectors: list[list[float]], expected_count: int, dimensions: int
    ) -> list[list[float]]:
        """
        Guard the vector contract.

        A vector store silently accepting a wrong-length or NaN vector
        produces either a crash deep inside the index or, worse, garbage
        neighbours.  Validating here keeps the failure attached to the
        embedding step that caused it.
        """
        if len(vectors) != expected_count:
            raise EmbeddingError(
                "The embedding provider returned an unexpected number of "
                "vectors.",
                internal_detail=(
                    f"expected {expected_count}, got {len(vectors)}"
                ),
            )
        for vector in vectors:
            if len(vector) != dimensions:
                raise EmbeddingError(
                    "The embedding provider returned a vector of the wrong "
                    "length.",
                    internal_detail=(
                        f"expected {dimensions}, got {len(vector)}"
                    ),
                )
            if any(
                not math.isfinite(value) or isinstance(value, bool)
                for value in vector
            ):
                raise EmbeddingError(
                    "The embedding provider returned a non-numeric vector.",
                    internal_detail="non-finite value in vector",
                )
        return vectors

    @staticmethod
    def l2_normalize(vector: list[float]) -> list[float]:
        """
        Scale a vector to unit length.

        A zero vector is returned unchanged: it carries no direction, and
        dividing by its norm would produce NaN.
        """
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return list(vector)
        return [v / norm for v in vector]

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"dimensions={self.dimensions}>"
        )
