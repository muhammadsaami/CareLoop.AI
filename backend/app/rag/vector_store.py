"""
CareLoop AI - ChromaDB Vector Store (Phase 3)

Thin, defensive wrapper around a single persistent ChromaDB collection.

WHY A WRAPPER
The RAG services should depend on a small, testable interface, not on the
Chroma SDK.  Every SDK failure is translated into `VectorStoreError` so an
unreachable or corrupt index becomes a clean 503 instead of a stack trace,
and no vendor exception ever reaches a route.

CHROMA CLIENT SINGLETON - IMPORTANT
`chromadb.PersistentClient` is effectively a PROCESS-WIDE singleton keyed on
its `Settings`.  Constructing two clients with different settings does not
give two isolated stores; the second call silently inherits the first one's
configuration.  `_client_for()` therefore caches one client per resolved
directory and every caller must go through the same `Settings` factory, so a
test can point the store at a temp directory without fighting a stale global.

TENANCY
One collection holds the chunks, and every chunk carries `patient_id` and
`discharge_document_id` in its metadata.  Isolation is enforced in two
independent layers: the `where` filter applied on EVERY read and delete
(`RagRetrievalService` always includes both IDs), and a PostgreSQL
ownership check performed before any Chroma call is made.  Chroma metadata
is therefore treated as a correctness requirement, never as a convenience.

HEALTHCARE SAFETY BOUNDARY
This module stores and returns verbatim clinician-authored text.  It does not
interpret, summarise, diagnose, or rewrite anything, and it never logs text,
document content, or queries.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CareLoopError,
    EmbeddingFingerprintMismatchError,
    VectorStoreError,
)
from app.core.logging import get_logger
from app.rag.chunking import TextChunk
from app.rag.embeddings.base import (
    _KEY_DIGEST,
    EmbeddingProvider,
    describe_fingerprint,
    fingerprint_mismatch,
)

logger = get_logger(__name__)

# Chroma's SQLite build accepts batches up to a few thousand rows.  Batching
# well below that keeps indexing a large document from tripping the limit and
# bounds peak memory for a multi-megabyte extracted_text.
_DEFAULT_BATCH_SIZE = 500

# One client per directory, guarded because FastAPI serves requests on a
# thread pool and chromadb is not documented as thread-safe on construction.
_CLIENT_LOCK = threading.Lock()
_CLIENTS: dict[str, chromadb.ClientAPI] = {}


def resolve_persist_directory(settings: Settings) -> Path:
    """
    Absolute path of the Chroma data directory.

    Relative paths resolve against `backend/`, so the store does not depend
    on the process working directory.
    """
    raw = Path(settings.chroma_persist_directory)
    if not raw.is_absolute():
        raw = Path(__file__).resolve().parents[2] / raw
    return raw


def _chroma_settings() -> ChromaSettings:
    """
    Build the SDK settings object.

    Telemetry is disabled because document vectors are derived from patient
    records; nothing about them should leave the host.  Anonymous usage IDs
    are not persisted.
    """
    return ChromaSettings(
        anonymized_telemetry=False,
        allow_reset=False,
    )


def _client_for(directory: Path) -> chromadb.ClientAPI:
    """
    Return a cached persistent client for `directory`.

    See the module docstring: chroma keeps a process-global client, so this
    cache is what makes a temp-directory client in a test actually isolated.
    """
    key = str(directory)
    with _CLIENT_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(
                    path=key, settings=_chroma_settings()
                )
            except Exception as exc:  # pragma: no cover - environment failure
                raise VectorStoreError(
                    internal_detail=f"chroma client init failed: {type(exc).__name__}",
                ) from exc
            _CLIENTS[key] = client
        return client


def reset_client_cache() -> None:
    """
    Drop the cached clients.

    Only for tests that need chroma's process-global client forgotten.
    Production code never calls this.
    """
    with _CLIENT_LOCK:
        _CLIENTS.clear()


def chunk_metadata(chunk: TextChunk) -> dict[str, Any]:
    """
    Build the metadata stored beside every vector.

    Rules that the rest of the system relies on:
      * `patient_id` and `discharge_document_id` are ALWAYS present, as
        strings, because the tenancy filter is built from them.
      * Chroma accepts only str / int / float / bool metadata values, so
        `None` is OMITTED rather than stored - a missing page is absent from
        the index, never guessed.
      * No document text, filename, or patient name is ever stored here.
    """
    metadata: dict[str, Any] = {
        "patient_id": str(chunk.patient_id),
        "discharge_document_id": str(chunk.document_id),
        "chunk_id": chunk.chunk_id,
        "chunk_index": int(chunk.index),
        "char_count": int(chunk.char_count),
    }
    if chunk.page is not None:
        metadata["source_page"] = int(chunk.page)
    if chunk.extraction_run_id is not None:
        metadata["extraction_run_id"] = str(chunk.extraction_run_id)
    if chunk.text_sha256:
        metadata["text_sha256"] = chunk.text_sha256
    return metadata


def tenancy_filter(patient_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    """
    The `where` clause every read and delete MUST use.

    Both IDs are required, joined with `$and`.  Building it in one place means
    a caller cannot accidentally filter on the patient alone and let a
    document from another document_id through.
    """
    return {
        "$and": [
            {"patient_id": str(patient_id)},
            {"discharge_document_id": str(document_id)},
        ]
    }


class ChromaVectorStore:
    """
    Persistent vector storage for document chunks.

    Vectors are always computed by the injected `EmbeddingProvider` and
    passed to chroma explicitly; the store never lets chroma embed text
    behind our back, so the provider contract stays in one place and the
    collection is created with no embedding function at all.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._settings = settings or get_settings()
        self._embeddings = embedding_provider
        self._batch_size = max(1, int(batch_size))
        self._collection: Any | None = None

    # ── Wiring ──────────────────────────────────────────────────────────────

    @property
    def embeddings(self) -> EmbeddingProvider:
        """Lazily resolve the provider so construction never fails on config."""
        if self._embeddings is None:
            from app.rag.embeddings.factory import get_embedding_provider

            self._embeddings = get_embedding_provider(settings=self._settings)
        return self._embeddings

    @property
    def collection_name(self) -> str:
        return self._settings.chroma_collection_name

    def _get_collection(self) -> Any:
        """
        Open the collection and prove its vectors came from this provider.

        The fingerprint check is what stops two different embedding models
        sharing one collection.  ChromaDB validates vector WIDTH and nothing
        else, and the `semantic` and `hashing` providers both emit 384
        dimensions, so without this check a provider switch would blend two
        unrelated vector spaces and return confidently wrong rankings with no
        error anywhere.

        Runs on every open rather than once per process: an operator can
        re-index or swap providers while the service is running, and a cached
        "verified" flag would keep answering with the stale verdict.  It is
        two local reads against an on-disk database.
        """
        if self._collection is not None:
            return self._collection
        client = _client_for(resolve_persist_directory(self._settings))
        try:
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                # No embedding function: every write and read supplies
                # embeddings computed by our provider, so chroma never needs
                # an ONNX model and never diverges from the configured
                # dimensions.
                embedding_function=None,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            raise VectorStoreError(
                internal_detail=(
                    f"collection open failed: {type(exc).__name__}"
                ),
            ) from exc
        self._verify_embedding_fingerprint(self._collection)
        return self._collection

    def _verify_embedding_fingerprint(self, collection: Any) -> None:
        """
        Compare the collection's recorded fingerprint with the configured one.

        Three cases:
          * no fingerprint, collection empty  -> stamp it (nothing to mix)
          * no fingerprint, collection non-empty -> refuse: the vectors' origin
            is unknown, and trusting them is exactly the silent mixing this
            exists to prevent
          * fingerprint present -> must match exactly, field by field
        """
        expected = self.embeddings.fingerprint
        try:
            stored = dict(collection.metadata or {})
        except Exception as exc:  # pragma: no cover - defensive
            raise VectorStoreError(
                internal_detail=(
                    f"collection metadata unreadable: {type(exc).__name__}"
                ),
            ) from exc

        stored_digest = stored.get(_KEY_DIGEST)

        if stored_digest is None:
            self._initialise_fingerprint(collection, expected)
            return

        problems = fingerprint_mismatch(expected, stored)
        if not problems:
            logger.debug(
                "Embedding fingerprint verified: collection=%s provider=%s "
                "model=%s dimensions=%s",
                self.collection_name,
                expected["careloop_embedding_provider"],
                expected["careloop_embedding_model"],
                expected["careloop_embedding_dimensions"],
            )
            return

        # Only configuration and model identifiers appear here - never document
        # text, queries, or patient identifiers.
        raise EmbeddingFingerprintMismatchError(
            f"The search index collection '{self.collection_name}' was built "
            f"with a different embedding model than the one now configured "
            f"({'; '.join(problems)}). Delete the collection and re-index every "
            f"document after changing embedding provider, model, or "
            f"dimensions; vectors from different models are not comparable. "
            f"Configured: {describe_fingerprint(expected)}. "
            f"Stored: {describe_fingerprint(stored)}.",
            internal_detail=(
                f"embedding fingerprint mismatch on "
                f"{self.collection_name}: {problems}"
            ),
        )

    def _initialise_fingerprint(
        self, collection: Any, expected: dict
    ) -> None:
        """
        Record the fingerprint on a collection that has none yet.

        Safe without re-indexing ONLY while the collection is empty: with no
        vectors there is nothing to be inconsistent with, so the label cannot
        be wrong.  A non-empty collection stamped here would be a guess about
        which model produced unknown vectors, so it is refused instead.
        """
        try:
            chunk_count = int(collection.count())
        except Exception as exc:  # pragma: no cover - defensive
            raise VectorStoreError(
                internal_detail=(
                    f"collection count failed: {type(exc).__name__}"
                ),
            ) from exc

        if chunk_count:
            raise EmbeddingFingerprintMismatchError(
                f"The search index collection '{self.collection_name}' already "
                f"holds {chunk_count} chunks but records no embedding model, so "
                f"it cannot be verified against the configured "
                f"{self.embeddings.name} provider. This index predates "
                f"fingerprinting. Delete the collection and re-index every "
                f"document; mixing vectors of unknown origin with newly "
                f"generated ones would corrupt retrieval results.",
                internal_detail=(
                    f"collection {self.collection_name} has {chunk_count} "
                    f"chunks and no {_KEY_DIGEST}"
                ),
            )

        try:
            # `modify` REPLACES the metadata mapping, and passing chroma's own
            # `hnsw:space` back to it raises "changing the distance function is
            # not supported".  The index keeps cosine internally; only the
            # reported metadata mapping is replaced, so only our own keys are
            # sent.
            collection.modify(metadata=expected)
        except Exception as exc:
            raise VectorStoreError(
                internal_detail=(
                    f"fingerprint init failed: {type(exc).__name__}"
                ),
            ) from exc

        logger.info(
            "Embedding fingerprint initialised: collection=%s provider=%s "
            "model=%s dimensions=%s",
            self.collection_name,
            expected["careloop_embedding_provider"],
            expected["careloop_embedding_model"],
            expected["careloop_embedding_dimensions"],
        )

    # ── Writes ──────────────────────────────────────────────────────────────

    def upsert_chunks(self, chunks: Sequence[TextChunk]) -> int:
        """
        Insert or update every chunk, batching to respect chroma's limits.

        Re-indexing the same document produces the same deterministic chunk
        IDs, so this is an upsert: it updates in place and never duplicates.
        """
        if not chunks:
            return 0

        collection = self._get_collection()
        total = 0
        for batch in self._batches(chunks, self._batch_size):
            try:
                vectors = self.embeddings.embed_documents(
                    [chunk.text for chunk in batch]
                )
            except CareLoopError:
                # An EmbeddingError already carries the right status code and
                # a client-safe message; re-wrapping it as a VectorStoreError
                # would turn a 502 into a misleading 503.
                raise
            except Exception as exc:
                raise VectorStoreError(
                    internal_detail=f"embedding batch failed: {type(exc).__name__}",
                ) from exc

            try:
                collection.upsert(
                    ids=[chunk.chunk_id for chunk in batch],
                    embeddings=vectors,
                    documents=[chunk.text for chunk in batch],
                    metadatas=[chunk_metadata(chunk) for chunk in batch],
                )
            except Exception as exc:
                raise VectorStoreError(
                    internal_detail=f"chroma upsert failed: {type(exc).__name__}",
                ) from exc
            total += len(batch)

        logger.info(
            "Vector store upsert: collection=%s chunks=%s provider=%s",
            self.collection_name,
            total,
            self.embeddings.name,
        )
        return total

    def delete_document(
        self, patient_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """
        Remove every chunk belonging to one document.

        Scoped by BOTH ids.  Even a wrong document_id cannot delete another
        patient's vectors.
        """
        collection = self._get_collection()
        try:
            collection.delete(where=tenancy_filter(patient_id, document_id))
        except Exception as exc:
            raise VectorStoreError(
                internal_detail=f"chroma delete failed: {type(exc).__name__}",
            ) from exc
        logger.info(
            "Vector store delete: collection=%s document=%s",
            self.collection_name,
            document_id,
        )

    # ── Reads ───────────────────────────────────────────────────────────────

    def query(
        self,
        query_text: str,
        patient_id: uuid.UUID,
        document_id: uuid.UUID,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Return the `n_results` chunks closest to `query_text`.

        The tenancy filter is a required argument rather than an option: it
        cannot be omitted, so there is no code path that queries the whole
        collection.  Distances are converted to a 0-1 `score` where higher is
        more similar, because cosine distance in [0, 2] is not a score a
        caller should have to interpret.
        """
        collection = self._get_collection()

        try:
            vector = self.embeddings.embed_query(query_text)
        except CareLoopError:
            # Preserves EmptyQueryError (422) and EmbeddingError (502) instead
            # of reporting a client mistake as an unavailable index.
            raise
        except Exception as exc:
            raise VectorStoreError(
                internal_detail=f"query embedding failed: {type(exc).__name__}",
            ) from exc

        try:
            raw = collection.query(
                query_embeddings=[vector],
                n_results=max(1, int(n_results)),
                where=tenancy_filter(patient_id, document_id),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise VectorStoreError(
                internal_detail=f"chroma query failed: {type(exc).__name__}",
            ) from exc

        return self._shape_query_result(raw)

    def count(
        self, patient_id: uuid.UUID, document_id: uuid.UUID
    ) -> int:
        """
        Number of stored chunks for one document, under the tenancy filter.

        Implemented with `get(include=[])` rather than `count()`: chroma 0.5.x
        has no `where` on `count()`, and an empty `include` means the document
        text is never read back just to be counted.
        """
        collection = self._get_collection()
        try:
            result = collection.get(
                where=tenancy_filter(patient_id, document_id),
                include=[],
            )
        except Exception as exc:
            raise VectorStoreError(
                internal_detail=f"chroma count failed: {type(exc).__name__}",
            ) from exc
        return len(result.get("ids") or [])

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _batches(
        items: Sequence[TextChunk], size: int
    ) -> Iterable[Sequence[TextChunk]]:
        for start in range(0, len(items), size):
            yield items[start : start + size]

    @staticmethod
    def _shape_query_result(raw: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Flatten chroma's column-oriented query response into per-hit dicts.

        Chroma returns `{"ids": [[...]], "documents": [[...]], ...}` - one
        inner list per query, since it supports batched queries.  We always
        send a single query, so only the first inner list is meaningful; an
        empty result set is normal and returns `[]` rather than raising.
        """
        if not raw:
            return []

        ids = (raw.get("ids") or [[]])[0]
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]

        hits: list[dict[str, Any]] = []
        for position, chunk_id in enumerate(ids):
            distance = (
                float(distances[position]) if position < len(distances) else 1.0
            )
            hits.append(
                {
                    "chunk_id": chunk_id,
                    "text": documents[position] if position < len(documents) else "",
                    "metadata": metadatas[position] if position < len(metadatas) else {},
                    "distance": distance,
                    # Cosine distance -> similarity in [0, 1]; clamped so a
                    # floating-point overshoot cannot exceed 1.0.
                    "score": max(0.0, min(1.0, 1.0 - distance)),
                }
            )
        return hits
