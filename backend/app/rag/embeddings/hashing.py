"""
CareLoop AI - Local Hashed Embedding Provider (Phase 3)

A dependency-free, fully offline embedding implementation based on the
hashing trick: each token is hashed into one of `dimensions` buckets, the
per-bucket counts are accumulated, and the result is L2-normalised.

WHY THIS EXISTS
  1. It needs no API key, no network, and no model download, so `pytest`
     never contacts an external service and runs identically on a laptop
     and in CI.
  2. It is deterministic: the same text always yields the same vector, which
     is what makes indexing reproducible and assertions meaningful.
  3. Because it compares shared TOKENS, a query for "paracetamol" actually
     ranks a chunk containing "Paracetamol" highly.  A plain hash of the
     whole string would share no terms at all and would return essentially
     random neighbours, which would make retrieval tests meaningless.

HONEST LIMITATION
This is a lexical (bag-of-words) representation, NOT a semantic one.  It
matches on shared words, so it will not connect "water tablet" to
"paracetamol", and it gives common-word collisions.  That is a deliberate
trade: a real semantic model means either a heavyweight local dependency
that downloads weights, or an external API that would put PHI in transit.
Swapping in a real provider later is a subclass plus one factory entry; see
`app.rag.embeddings.factory`.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

from app.core.config import Settings
from app.core.exceptions import EmbeddingError
from app.core.logging import get_logger
from app.rag.embeddings.base import STOPWORDS, EmbeddingProvider, tokenize

logger = get_logger(__name__)

# Fixed so the hash is stable across processes and Python versions.
# `hash()` is NOT used: it is randomised per process for strings (PYTHONHASHSEED).
_HASH_SEED = b"careloop-rag-v1"

# Odd 64-bit constants (SplitMix64 finaliser) used to derive additional
# projections from a single digest.
_MIX_A = 0xBF58476D1CE4E5B9
_MIX_B = 0x94D049BB133111EB


def _mix(value: int, salt: int) -> int:
    """SplitMix64 finaliser: cheap, deterministic, well-distributed."""
    masked = value & 0xFFFFFFFFFFFFFFFF
    masked ^= (masked >> 30) * _MIX_A & 0xFFFFFFFFFFFFFFFF
    masked ^= (masked >> 27) * _MIX_B & 0xFFFFFFFFFFFFFFFF
    masked ^= (masked >> 31)
    return (masked + salt * _MIX_A) & 0xFFFFFFFFFFFFFFFF


class HashingEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline bag-of-words embeddings."""

    name = "hashing"

    #: Identifies the ALGORITHM, not a downloadable artefact.  Bumping this
    #: string is how a future change to the hashing scheme announces that
    #: existing collections are no longer comparable and must be re-indexed.
    model_id = "hashing-bag-of-words-v1"

    #: Calibrated against a representative discharge summary: a query sharing
    #: real content scored >= 0.118 while one sharing nothing scored <= 0.076,
    #: so 0.10 sits in that gap.  Much lower than the semantic provider's
    #: floor because a bag-of-words model spans a far wider score range.
    default_min_score = 0.10

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._dimensions = int(self._settings.rag_embedding_dimensions)
        self._projections = int(self._settings.rag_embedding_projections)
        if self._dimensions < 8:
            raise EmbeddingError(
                "RAG_EMBEDDING_DIMENSIONS is too small to be usable.",
                internal_detail=f"dimensions={self._dimensions}",
            )
        if self._projections < 1:
            raise EmbeddingError(
                "RAG_EMBEDDING_PROJECTIONS must be at least 1.",
                internal_detail=f"projections={self._projections}",
            )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def vector_space_config(self) -> str:
        """
        Every setting that changes the produced vectors.

        The stopword list is hashed in because removing a word from it changes
        the vector for any text containing that word, which makes an existing
        collection quietly wrong rather than obviously broken.
        """
        stopword_digest = hashlib.sha256(
            "\n".join(sorted(STOPWORDS)).encode("utf-8")
        ).hexdigest()[:12]
        return (
            f"projections={self._projections};"
            f"stopwords={stopword_digest};"
            f"tokenizer=word-re-v1"
        )

    def is_configured(self) -> bool:
        """
        Always true: this provider has no external dependency.

        It is the default precisely so a fresh checkout can index and
        retrieve without provisioning anything.
        """
        return True

    # ── Vectorisation ───────────────────────────────────────────────────────

    def _projections_for(self, token: str) -> list[tuple[int, float]]:
        """
        Map one token to several (bucket index, signed weight) pairs.

        WHY MORE THAN ONE
        With a single projection, two different tokens can land in the same
        bucket with opposite signs and cancel each other exactly.  Observed in
        practice: a document containing "Warfarin" scored 0.0 against the
        query "warfarin", because "takes" hashed to the same bucket with the
        opposite sign.  A false negative like that silently discards the
        correct answer, which is far worse than a false positive.

        Spreading each token over several independently-seeded buckets means a
        collision can cancel at most one contribution instead of the whole
        signal, so a true match still ranks highly.  This is the standard
        multi-projection feature-hashing technique.
        """
        digest = hashlib.blake2b(
            token.encode("utf-8"), key=_HASH_SEED, digest_size=8
        ).digest()
        raw = int.from_bytes(digest, "big")

        # Derive every projection from the one digest so the vector stays a
        # pure function of the token - no extra hashing cost, and identical
        # output on every run and every machine.
        projections: list[tuple[int, float]] = []
        for p in range(self._projections):
            mixed = raw if p == 0 else _mix(raw, p)
            index = mixed % self._dimensions
            sign = 1.0 if (mixed >> 63) & 1 else -1.0
            pair = (index, sign)
            if pair not in projections:
                projections.append(pair)
        return projections

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in tokenize(text):
            if token in STOPWORDS:
                continue
            for index, sign in self._projections_for(token):
                vector[index] += sign
        return self.l2_normalize(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [self._embed(t or "") for t in texts]
        return self._validate(vectors, len(texts), self._dimensions)

    def embed_query(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise EmbeddingError(
                "Cannot embed an empty query.",
                internal_detail="empty query text",
            )
        vector = self._embed(text)
        return self._validate([vector], 1, self._dimensions)[0]
