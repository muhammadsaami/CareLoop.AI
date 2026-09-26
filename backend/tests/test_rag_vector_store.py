"""
CareLoop AI - Vector Store Tests (Phase 3)

Focus: the tenancy guarantees.  A vector store that leaks one patient's
passages to another is the worst failure this system can have, so the
isolation tests here are deliberately blunt - they attempt real cross-patient
reads and deletes rather than trusting that the filter was applied.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.core.exceptions import VectorStoreError
from app.rag.chunking import DocumentChunker
from app.rag.embeddings import get_embedding_provider
from app.rag.vector_store import (
    ChromaVectorStore,
    chunk_metadata,
    resolve_persist_directory,
    tenancy_filter,
)

DOC = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
OTHER_DOC = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
PAT = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
OTHER_PAT = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")

TEXT = (
    "--- PAGE 1 ---\n"
    "Paracetamol 500 mg every six hours as required for pain relief.\n"
    "--- PAGE 2 ---\n"
    "Change the wound dressing daily and keep the area dry.\n"
)


@pytest.fixture
def settings(tmp_path):
    directory = tmp_path / "chroma"
    directory.mkdir(parents=True, exist_ok=True)
    return Settings(
        secret_key="x" * 64,
        chroma_persist_directory=str(directory),
        rag_chunk_size=200,
        rag_chunk_overlap=30,
    )


@pytest.fixture
def store(settings):
    return ChromaVectorStore(settings, get_embedding_provider(settings=settings))


@pytest.fixture
def chunks(settings):
    return DocumentChunker(settings).chunk_document(
        document_id=DOC, patient_id=PAT, text=TEXT
    )


# ── Write / read round trip ──────────────────────────────────────────────────


def test_upsert_then_count(store, chunks):
    assert store.upsert_chunks(chunks) == len(chunks)
    assert store.count(PAT, DOC) == len(chunks)


def test_upsert_is_idempotent(store, chunks):
    """Re-indexing must overwrite, never duplicate."""
    store.upsert_chunks(chunks)
    store.upsert_chunks(chunks)
    assert store.count(PAT, DOC) == len(chunks)


def test_upsert_of_nothing_is_a_no_op(store):
    assert store.upsert_chunks([]) == 0


def test_query_returns_verbatim_text_with_metadata(store, chunks):
    store.upsert_chunks(chunks)
    hits = store.query("paracetamol pain relief", PAT, DOC, n_results=5)
    assert hits
    top = hits[0]
    assert top["text"] in {c.text for c in chunks}
    assert top["metadata"]["patient_id"] == str(PAT)
    assert top["metadata"]["discharge_document_id"] == str(DOC)
    assert 0.0 <= top["score"] <= 1.0
    assert "paracetamol" in top["text"].lower()


def test_scores_are_ordered_best_first(store, chunks):
    store.upsert_chunks(chunks)
    hits = store.query("wound dressing", PAT, DOC, n_results=5)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_data_survives_a_new_store_instance(settings, chunks):
    """The index is persistent, not per-process state."""
    ChromaVectorStore(settings, get_embedding_provider(settings=settings)).upsert_chunks(
        chunks
    )
    reopened = ChromaVectorStore(
        settings, get_embedding_provider(settings=settings)
    )
    assert reopened.count(PAT, DOC) == len(chunks)


def test_n_results_larger_than_corpus_is_safe(store, chunks):
    store.upsert_chunks(chunks)
    assert len(store.query("dressing", PAT, DOC, n_results=500)) == len(chunks)


def test_query_on_empty_index_returns_empty_list(store):
    assert store.query("anything", PAT, DOC, n_results=5) == []


# ── TENANCY: the tests that matter most ──────────────────────────────────────


def test_cannot_read_another_patients_document(store, chunks):
    """Same document id, wrong patient: nothing may come back."""
    store.upsert_chunks(chunks)
    assert store.query("paracetamol", OTHER_PAT, DOC, n_results=10) == []
    assert store.count(OTHER_PAT, DOC) == 0


def test_cannot_read_a_different_document(store, chunks):
    store.upsert_chunks(chunks)
    assert store.query("paracetamol", PAT, OTHER_DOC, n_results=10) == []
    assert store.count(PAT, OTHER_DOC) == 0


def test_cannot_delete_another_patients_document(store, chunks):
    """A wrong patient id must not be able to destroy the index."""
    store.upsert_chunks(chunks)
    store.delete_document(OTHER_PAT, DOC)
    assert store.count(PAT, DOC) == len(chunks), "cross-tenant delete succeeded"


def test_two_patients_coexist_without_bleeding(store, settings, chunks):
    """A shared collection must still keep two patients fully separate."""
    own = DocumentChunker(settings).chunk_document(
        document_id=OTHER_DOC,
        patient_id=OTHER_PAT,
        text="--- PAGE 1 ---\nIbuprofen 200 mg for the other patient only.",
    )
    store.upsert_chunks(chunks)
    store.upsert_chunks(own)

    mine = store.query("paracetamol", PAT, DOC, n_results=10)
    theirs = store.query("ibuprofen", OTHER_PAT, OTHER_DOC, n_results=10)

    assert all("ibuprofen" not in h["text"].lower() for h in mine)
    assert all("paracetamol" not in h["text"].lower() for h in theirs)
    assert store.count(PAT, DOC) == len(chunks)
    assert store.count(OTHER_PAT, OTHER_DOC) == len(own)


def test_filter_requires_both_identifiers():
    """The filter is built in one place and always carries both IDs."""
    clause = tenancy_filter(PAT, DOC)
    rendered = str(clause)
    assert str(PAT) in rendered and str(DOC) in rendered
    assert "$and" in clause


def test_delete_is_scoped_to_one_document(store, settings, chunks):
    own = DocumentChunker(settings).chunk_document(
        document_id=OTHER_DOC,
        patient_id=PAT,
        text="--- PAGE 1 ---\nA different document for the same patient.",
    )
    store.upsert_chunks(chunks)
    store.upsert_chunks(own)
    store.delete_document(PAT, DOC)
    assert store.count(PAT, DOC) == 0
    assert store.count(PAT, OTHER_DOC) == len(own), "sibling document was deleted"


# ── Metadata contract ────────────────────────────────────────────────────────


def test_metadata_omits_missing_page_rather_than_guessing(chunks):
    """A page-less chunk must have no source_page key at all."""
    unnumbered = DocumentChunker(
        Settings(secret_key="x" * 64, rag_chunk_size=200, rag_chunk_overlap=30)
    ).chunk_document(
        document_id=DOC, patient_id=PAT, text="Text with no page marker at all."
    )
    assert chunk_metadata(unnumbered[0]).get("source_page") is None
    assert "source_page" not in chunk_metadata(unnumbered[0])


def test_metadata_is_chroma_compatible_types(chunks):
    """Chroma rejects None, lists and dicts; only scalars are allowed."""
    for chunk in chunks:
        for key, value in chunk_metadata(chunk).items():
            assert isinstance(value, (str, int, float, bool)), key


def test_metadata_contains_no_document_text(chunks):
    for chunk in chunks:
        rendered = str(chunk_metadata(chunk)).lower()
        assert "paracetamol" not in rendered
        assert "dressing" not in rendered


def test_metadata_carries_extraction_run_when_present(chunks):
    run_id = uuid.uuid4()
    with_run = DocumentChunker(
        Settings(secret_key="x" * 64, rag_chunk_size=200, rag_chunk_overlap=30)
    ).chunk_document(
        document_id=DOC, patient_id=PAT, text=TEXT, extraction_run_id=run_id
    )
    assert chunk_metadata(with_run[0])["extraction_run_id"] == str(run_id)


# ── Failure handling ─────────────────────────────────────────────────────────


def test_query_time_sdk_failure_becomes_a_domain_error(settings, monkeypatch):
    """A vendor failure mid-query must surface as VectorStoreError, not a raw SDK error."""
    store = ChromaVectorStore(settings, get_embedding_provider(settings=settings))

    class _Exploding:
        def query(self, *args, **kwargs):
            raise RuntimeError("chroma exploded: /secret/path/chroma.sqlite3")

    monkeypatch.setattr(store, "_get_collection", lambda: _Exploding())
    with pytest.raises(VectorStoreError) as exc:
        store.query("x", PAT, DOC)
    # Only the exception TYPE is logged; the vendor message may embed paths or
    # document text, so it must not be propagated even into internal_detail.
    assert "chroma query failed: RuntimeError" in (exc.value.internal_detail or "")
    assert "chroma exploded" not in (exc.value.internal_detail or "")
    assert "/secret/path" not in str(exc.value)


def test_failure_opening_the_collection_becomes_a_domain_error(
    settings, monkeypatch
):
    """An unusable index directory is a 503, never an unhandled crash."""
    import chromadb

    store = ChromaVectorStore(settings, get_embedding_provider(settings=settings))

    def _explode(*args, **kwargs):
        raise RuntimeError("cannot open /secret/path")

    monkeypatch.setattr(chromadb, "PersistentClient", _explode)
    with pytest.raises(VectorStoreError):
        store.count(PAT, DOC)


def test_write_time_sdk_failure_becomes_a_domain_error(settings, monkeypatch, chunks):
    store = ChromaVectorStore(settings, get_embedding_provider(settings=settings))

    class _Exploding:
        def upsert(self, *args, **kwargs):
            raise RuntimeError("disk full on /secret/path")

    monkeypatch.setattr(store, "_get_collection", lambda: _Exploding())
    with pytest.raises(VectorStoreError):
        store.upsert_chunks(chunks)


def test_persist_directory_is_absolute(settings):
    assert resolve_persist_directory(settings).is_absolute()


def test_relative_persist_directory_resolves_against_backend():
    relative = Settings(secret_key="x" * 64, chroma_persist_directory="chroma_data")
    resolved = resolve_persist_directory(relative)
    assert resolved.is_absolute()
    assert resolved.name == "chroma_data"
    assert resolved.parent.name == "backend"
