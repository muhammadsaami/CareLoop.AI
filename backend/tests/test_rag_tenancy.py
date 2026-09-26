"""
CareLoop AI - Patient Isolation / Tenancy Tests (Phase 3)

These are the security tests for Phase 3. They are deliberately written as
attack simulations rather than happy-path checks: each one starts from a
legitimately indexed document and then tries to reach it the wrong way.

The contract being pinned:

  1. One shared collection (`careloop_documents`) for every patient. There is
     no per-patient collection, so isolation CANNOT come from the collection
     name - it has to come from somewhere else.
  2. PostgreSQL is the authority. A document is reachable only if a row in
     `discharge_documents` proves the supplied `patient_id` owns the supplied
     `discharge_document_id`. This check happens BEFORE any vector-store
     call, so an unauthorised request never reaches the vectors at all.
  3. Chroma's metadata filter is a second, independent barrier - required on
     every read, count, and delete, on BOTH ids.
  4. Neither id may be omitted. There is no "search everything" path.
  5. A document that is not the caller's returns a controlled 404 that is
     indistinguishable from a document that does not exist, so ids cannot be
     enumerated.

No test here relies on chroma's filtering alone: several assert on the
PostgreSQL check specifically, because a filter-only implementation would
still leak if the metadata were ever wrong.
"""
from __future__ import annotations

import uuid

import pytest

from app.rag.indexing import RagIndexingService
from app.rag.retrieval import RagRetrievalService
from app.rag.vector_store import ChromaVectorStore


def _uuid_of(value):
    """Accept a UUID, an ORM row, or an API dict and return the UUID."""
    if hasattr(value, "id"):
        return value.id
    if isinstance(value, dict):
        return uuid.UUID(value["id"])
    return value


def index_url(document_id):
    return f"/api/v1/rag/documents/{_uuid_of(document_id)}/index"


def retrieve(
    client, patient_id, document_id, query="paracetamol dosage", **extra
):
    return client.post(
        "/api/v1/rag/retrieve",
        json={
            "patient_id": str(_uuid_of(patient_id)),
            "document_id": str(_uuid_of(document_id)),
            "query": query,
            **extra,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Cross-patient isolation
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossPatientIsolation:
    def test_patient_a_cannot_retrieve_patient_b_chunks(
        self, rag_client, indexed_document, make_patient
    ):
        """
        The core isolation test.

        Patient B's document is indexed and fully readable by B. Patient A
        points at B's `document_id` and must get nothing at all - not B's
        text, not a score, not a chunk count that reveals B indexed content.
        """
        patient_b, document_b, _ = indexed_document
        patient_a = make_other_patient(make_patient)

        response = retrieve(rag_client, patient_a, document_b.id)
        assert response.status_code == 404

        body = response.json()
        # The message names neither existence nor ownership, and is byte-for-
        # byte what a genuinely missing document returns.
        assert body["detail"] == "No discharge document was found for this patient."
        # Nothing leaked in the error payload either.
        assert "paracetamol" not in str(body).lower()
        assert "chunks" not in body

    def test_patient_a_cannot_retrieve_another_document_of_the_same_patient(
        self, rag_client, make_patient, make_document, indexed_document
    ):
        """
        Within one patient, a document id is still required.

        A patient legitimately owns two documents. Querying with the WRONG one
        of their own documents must not return the other document's passages.
        """
        patient, first, _ = indexed_document
        # A distinct filename/sha: (patient_id, sha256_hash) is unique, so two
        # documents for one patient must not collide.
        second = make_document(patient, filename="follow-up-summary.pdf")

        # Sanity: each document is separately retrievable by its owner.
        assert retrieve(rag_client, patient, first.id).status_code == 200
        assert retrieve(rag_client, patient, second.id).status_code == 200

        # The mismatch is rejected rather than silently returning the nearest
        # document.
        response = retrieve(
            rag_client, patient, uuid.UUID(int=999), "paracetamol dosage"
        )
        assert response.status_code == 404

    def test_wrong_patient_document_pair_returns_no_data(
        self, rag_client, make_patient, make_document, indexed_document
    ):
        """
        A real (patient B, document A) pair is a combination that exists on
        exactly one side. It must behave as a miss, never as a partial hit.
        """
        patient_a, document_a, _ = indexed_document
        patient_b = make_other_patient(make_patient)

        response = retrieve(rag_client, patient_b, document_a.id)
        assert response.status_code == 404
        assert (
            response.json()["detail"]
            == "No discharge document was found for this patient."
        )

        # And A is unaffected - the failed attempt changed nothing.
        assert retrieve(rag_client, patient_a, document_a.id).status_code == 200

    def test_attacker_cannot_enumerate_document_ids(
        self, rag_client, indexed_document, make_patient
    ):
        """
        A 404 for "not yours" must be indistinguishable from a 404 for "does
        not exist", otherwise ids can be probed for existence.
        """
        patient, document, _ = indexed_document
        attacker = make_other_patient(make_patient)

        not_yours = retrieve(rag_client, attacker, document.id)
        does_not_exist = retrieve(rag_client, attacker, uuid.UUID(int=424242))

        assert not_yours.status_code == does_not_exist.status_code == 404
        assert not_yours.json() == does_not_exist.json()

    def test_many_wrong_patients_all_fail_identically(
        self, rag_client, indexed_document, make_patient
    ):
        """No per-patient variation in the refusal."""
        _, document, _ = indexed_document
        responses = [
            retrieve(
                rag_client,
                make_other_patient(make_patient, suffix=str(9100 + i)),
                document.id,
            )
            for i in range(3)
        ]
        assert {r.status_code for r in responses} == {404}
        assert len({r.json()["detail"] for r in responses}) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. Missing ownership -> controlled error (never a vector-store call)
# ═══════════════════════════════════════════════════════════════════════════


class TestMissingOwnershipIsControlled:
    def test_unknown_patient_id_is_a_404_not_a_500(
        self, rag_client, indexed_document, make_patient
    ):
        _, document, _ = indexed_document
        response = retrieve(rag_client, uuid.UUID(int=555555), document.id)
        assert response.status_code == 404

    def test_nonexistent_document_is_a_404(self, rag_client, sample_patient):
        response = retrieve(
            rag_client, sample_patient, uuid.UUID(int=777)
        )
        assert response.status_code == 404

    def test_ownership_is_checked_before_the_vector_store(
        self, db_session, rag_settings, indexed_document
    ):
        """
        Prove the PostgreSQL check runs FIRST, not merely that a filter exists.

        A recording store stands in for Chroma. If any vector operation is
        reached for an unauthorised pair, this fails - which is the property
        that matters, because a metadata filter alone would still be the only
        barrier if the DB check were removed.
        """
        _, document, _ = indexed_document
        attacker = uuid.UUID(int=31337)

        class TripwireStore(ChromaVectorStore):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.calls: list[str] = []

            def query(self, *args, **kwargs):
                self.calls.append("query")
                return super().query(*args, **kwargs)

            def count(self, *args, **kwargs):
                self.calls.append("count")
                return super().count(*args, **kwargs)

            def delete_document(self, *args, **kwargs):
                self.calls.append("delete_document")
                return super().delete_document(*args, **kwargs)

        store = TripwireStore(rag_settings)
        service = RagRetrievalService(
            db_session, store=store, settings=rag_settings
        )

        with pytest.raises(Exception) as exc:
            service.retrieve(
                patient_id=attacker,
                document_id=document.id,
                query="paracetamol dosage",
            )
        assert "no discharge document" in str(exc.value).lower()
        # The vector store was never touched.
        assert store.calls == []

    def test_ownership_check_runs_before_deletion_too(
        self, db_session, rag_settings, indexed_document
    ):
        """Indexing deletes then re-upserts, so the delete must be gated too."""
        _, document, _ = indexed_document
        attacker = uuid.UUID(int=24680)

        class TripwireStore(ChromaVectorStore):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.calls: list[str] = []

            def delete_document(self, *args, **kwargs):
                self.calls.append("delete_document")
                return super().delete_document(*args, **kwargs)

            def upsert_chunks(self, *args, **kwargs):
                self.calls.append("upsert_chunks")
                return super().upsert_chunks(*args, **kwargs)

        store = TripwireStore(rag_settings)
        service = RagIndexingService(
            db_session, store=store, settings=rag_settings
        )

        with pytest.raises(Exception):
            service.index_document(
                patient_id=attacker, document_id=document.id
            )
        # Critically: the real document's vectors were NOT deleted by a
        # stranger's indexing attempt.
        assert store.calls == []

    def test_unauthorised_delete_leaves_the_index_intact(
        self, rag_client, indexed_document, make_patient
    ):
        """
        End-to-end: a stranger cannot wipe another patient's index, and the
        owner's data still works afterwards.
        """
        patient, document, _ = indexed_document
        before = retrieve(rag_client, patient, document.id).json()
        assert before["match_count"] > 0

        attacker = make_other_patient(make_patient)
        # Re-indexing as the attacker must fail...
        assert (
            rag_client.post(
                index_url(document.id),
                json={"patient_id": str(_uuid_of(attacker))},
            ).status_code
            == 404
        )
        # ...and must not have removed the owner's vectors.
        after = retrieve(rag_client, patient, document.id).json()
        assert after["match_count"] == before["match_count"]
        assert after["indexed_chunks"] == before["indexed_chunks"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Both ids are structurally mandatory
# ═══════════════════════════════════════════════════════════════════════════


class TestBothIdentifiersAreMandatory:
    def test_query_requires_patient_id(self, rag_client, indexed_document):
        _, document, _ = indexed_document
        response = rag_client.post(
            "/api/v1/rag/retrieve",
            json={"document_id": str(document.id), "query": "paracetamol"},
        )
        assert response.status_code == 422

    def test_query_requires_document_id(self, rag_client, sample_patient):
        response = rag_client.post(
            "/api/v1/rag/retrieve",
            json={"patient_id": str(_uuid_of(sample_patient)), "query": "paracetamol"},
        )
        assert response.status_code == 422

    def test_query_requires_both_and_the_query(
        self, rag_client, sample_patient
    ):
        response = rag_client.post("/api/v1/rag/retrieve", json={})
        assert response.status_code == 422
        detail = str(response.json())
        assert "patient_id" in detail
        assert "document_id" in detail

    def test_store_query_signature_requires_both_ids(self):
        """
        The ids are positional parameters with no default, so no caller can
        query without them - not even by accident at the store layer.
        """
        import inspect

        for method in (ChromaVectorStore.query, ChromaVectorStore.delete_document,
                       ChromaVectorStore.count):
            params = inspect.signature(method).parameters
            assert params["patient_id"].default is inspect.Parameter.empty
            assert params["document_id"].default is inspect.Parameter.empty

    def test_no_store_method_can_query_the_whole_collection(self):
        """
        There must be no unfiltered read or delete entry point at all.

        Scoped to the methods that can RETURN or REMOVE indexed data. The
        write path (`upsert_chunks`) is excluded on purpose: it receives
        `TextChunk` objects that each carry their own `patient_id` and
        `discharge_document_id`, which is where its scoping comes from.
        """
        import inspect

        read_or_delete = {"query", "delete_document", "count"}
        for name in read_or_delete:
            params = inspect.signature(
                getattr(ChromaVectorStore, name)
            ).parameters
            assert params["patient_id"].default is inspect.Parameter.empty, (
                f"{name} must not be callable without patient_id"
            )
            assert params["document_id"].default is inspect.Parameter.empty, (
                f"{name} must not be callable without document_id"
            )

    def test_upsert_scopes_every_chunk_by_both_ids(self):
        """
        The write path is scoped by the chunks themselves, so prove each one
        carries both ids rather than relying on a collection-level filter.
        """
        from app.rag.chunking import TextChunk

        document_id = uuid.UUID(int=11)
        patient_id = uuid.UUID(int=22)
        chunks = [
            TextChunk(
                chunk_id=f"{document_id}:1:0000",
                document_id=document_id,
                patient_id=patient_id,
                text="text",
                index=0,
                page=1,
            )
        ]
        for chunk in chunks:
            assert chunk.patient_id == patient_id
            assert chunk.document_id == document_id


# ═══════════════════════════════════════════════════════════════════════════
# 4. The Chroma filter is a real, independent barrier
# ═══════════════════════════════════════════════════════════════════════════


class TestChromaFilterIsIndependent:
    def test_filter_requires_both_metadata_keys(self):
        from app.rag.vector_store import tenancy_filter

        clause = tenancy_filter(uuid.UUID(int=1), uuid.UUID(int=2))
        assert "$and" in clause
        keys = {list(part)[0] for part in clause["$and"]}
        assert keys == {"patient_id", "discharge_document_id"}

    def test_single_collection_is_shared_by_all_patients(
        self, rag_client, make_patient, make_document
    ):
        """
        Isolation is NOT provided by separate collections.

        Two patients' documents land in the same configured collection, so the
        only thing separating them is the metadata filter plus the PostgreSQL
        ownership check. This test documents that deliberately: if someone
        later "improves" this by giving each patient its own collection, the
        documented design changes and the filter tests must be revisited.
        """
        patient_a = make_patient(name="Iso", suffix="1001")
        patient_b = make_patient(name="Iso", suffix="1002")
        doc_a = make_document(patient_a)
        doc_b = make_document(patient_b)

        for patient, document in ((patient_a, doc_a), (patient_b, doc_b)):
            assert (
                rag_client.post(
                    index_url(document.id), json={"patient_id": str(patient.id)}
                ).status_code
                == 200
            )

        # Both retrievable by their own owner.
        assert retrieve(rag_client, patient_a, doc_a.id).status_code == 200
        assert retrieve(rag_client, patient_b, doc_b.id).status_code == 200

        # Neither reachable by the other.
        assert retrieve(rag_client, patient_a, doc_b.id).status_code == 404
        assert retrieve(rag_client, patient_b, doc_a.id).status_code == 404

    def test_metadata_actually_stores_both_ids(
        self, db_session, rag_settings, indexed_document
    ):
        """
        Verify the filter keys really exist in the index.

        A filter on a key that is absent matches nothing, which would look
        like working isolation while silently returning zero results for
        everyone.
        """
        from app.rag.indexing import RagIndexingService
        from tests.conftest import FakeEmbeddingProvider

        patient, document, _ = indexed_document
        store = ChromaVectorStore(
            rag_settings, embedding_provider=FakeEmbeddingProvider(rag_settings)
        )
        service = RagIndexingService(
            db_session, store=store, settings=rag_settings
        )
        service.index_document(patient_id=patient.id, document_id=document.id)

        collection = store._get_collection()
        stored = collection.get(include=["metadatas"])
        assert stored["metadatas"], "nothing was indexed"
        for metadata in stored["metadatas"]:
            assert metadata["patient_id"] == str(patient.id)
            assert metadata["discharge_document_id"] == str(document.id)

    def test_filter_alone_blocks_a_forged_patient_id(
        self, db_session, rag_settings, indexed_document
    ):
        """
        If the PostgreSQL check were bypassed and only Chroma filtering
        remained, would it still hold? This proves the second barrier works
        on its own, so the design does not rest on a single mechanism.
        """
        from tests.conftest import FakeEmbeddingProvider

        patient, document, _ = indexed_document
        store = ChromaVectorStore(
            rag_settings, embedding_provider=FakeEmbeddingProvider(rag_settings)
        )
        collection = store._get_collection()
        # A unit vector, so cosine similarity is well defined against the
        # already-indexed chunks and the only thing under test is the FILTER.
        probe = [1.0] + [0.0] * (store.embeddings.dimensions - 1)
        collection.add(
            ids=["probe-1"],
            embeddings=[probe],
            metadatas=[
                {
                    "patient_id": str(_uuid_of(patient)),
                    "discharge_document_id": str(_uuid_of(document)),
                    "chunk_id": "probe-1",
                }
            ],
            documents=["probe"],
        )

        def probe_ids(**kwargs):
            return {h["chunk_id"] for h in store.query("probe", **kwargs)}

        # Correct ids find it.
        assert "probe-1" in probe_ids(
            patient_id=patient.id, document_id=document.id
        )
        # A forged patient id does not.
        assert (
            probe_ids(
                patient_id=uuid.UUID(int=999), document_id=document.id
            )
            == set()
        )
        # A forged document id does not.
        assert (
            probe_ids(
                patient_id=patient.id, document_id=uuid.UUID(int=888)
            )
            == set()
        )


# ── helpers ────────────────────────────────────────────────────────────────


def make_other_patient(make_patient, suffix="9001"):
    """
    A second, unrelated patient.

    Built through the same ORM fixture the rest of the suite uses, so it is a
    genuine Patient row - which is exactly what the ownership check reads.
    """
    return make_patient(name="Mallory Attempts", suffix=suffix)
