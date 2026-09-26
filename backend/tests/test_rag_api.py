"""
CareLoop AI - RAG API Tests (Phase 3)

End-to-end coverage of the two Phase 3 endpoints, with emphasis on the
guarantees a clinical caller depends on: correct page citations, strict
patient/document isolation, no answer generation, and no PHI in responses or
logs.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from app.models.discharge_document import ProcessingStatus

RETRIEVE = "/api/v1/rag/retrieve"


def index_url(document_id):
    return f"/api/v1/rag/documents/{document_id}/index"


def retrieve(client, patient, document, query, **extra):
    payload = {
        "patient_id": str(patient.id),
        "document_id": str(document.id),
        "query": query,
    }
    payload.update(extra)
    return client.post(RETRIEVE, json=payload)


# ═══════════════════════════════════════════════════════════════════════════
# Indexing
# ═══════════════════════════════════════════════════════════════════════════


class TestIndexing:
    def test_indexes_a_completed_document(self, rag_client, indexed_document):
        _, document, body = indexed_document
        assert body["chunks_indexed"] > 0
        assert body["pages_covered"] == 2
        assert body["document_id"] == str(document.id)
        assert body["embedding_provider"] == "fake"
        # The injected test double is 1024-dimensional. This asserts the
        # response reports the provider's ACTUAL width rather than a
        # configured guess, which is what would silently corrupt an index if
        # a provider's real output disagreed with RAG_EMBEDDING_DIMENSIONS.
        assert body["embedding_dimensions"] == 1024

    def test_index_response_contains_no_document_text(
        self, rag_client, indexed_document
    ):
        _, _, body = indexed_document
        rendered = str(body).lower()
        assert "paracetamol" not in rendered
        assert "physiotherapy" not in rendered

    def test_reindexing_is_idempotent(self, rag_client, indexed_document):
        patient, document, first = indexed_document
        response = rag_client.post(
            index_url(document.id), json={"patient_id": str(patient.id)}
        )
        assert response.status_code == 200
        assert response.json()["chunks_indexed"] == first["chunks_indexed"]

    def test_cannot_index_another_patients_document(
        self, rag_client, make_patient, make_document
    ):
        owner = make_patient(name="Owner", suffix="0200")
        intruder = make_patient(name="Intruder", suffix="0201")
        document = make_document(owner)

        response = rag_client.post(
            index_url(document.id), json={"patient_id": str(intruder.id)}
        )
        # Same 404 shape as a genuinely missing document.
        assert response.status_code == 404

    def test_unknown_document_returns_404(self, rag_client, make_patient):
        patient = make_patient(name="Ghost", suffix="0202")
        response = rag_client.post(
            index_url(uuid.uuid4()), json={"patient_id": str(patient.id)}
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "status",
        [
            ProcessingStatus.PENDING,
            ProcessingStatus.PROCESSING,
            ProcessingStatus.FAILED,
        ],
    )
    def test_refuses_to_index_an_unfinished_document(
        self, rag_client, make_patient, make_document, status
    ):
        """Indexing a failed document would return partial text as the clinician's words."""
        patient = make_patient(name="Pending", suffix="0203")
        document = make_document(patient, processing_status=status)
        response = rag_client.post(
            index_url(document.id), json={"patient_id": str(patient.id)}
        )
        assert response.status_code == 422

    def test_refuses_to_index_a_document_with_no_text(
        self, rag_client, make_patient, make_document
    ):
        patient = make_patient(name="NoText", suffix="0204")
        document = make_document(patient, extracted_text="")
        response = rag_client.post(
            index_url(document.id), json={"patient_id": str(patient.id)}
        )
        assert response.status_code == 422

    def test_refuses_to_index_text_that_yields_no_chunks(
        self, rag_client, make_patient, make_document
    ):
        patient = make_patient(name="Noise", suffix="0205")
        document = make_document(patient, extracted_text="x")
        response = rag_client.post(
            index_url(document.id), json={"patient_id": str(patient.id)}
        )
        assert response.status_code == 422

    def test_upload_does_not_automatically_index(self, rag_client, make_patient, make_document):
        """
        Indexing is explicit. Phase 2 behaviour must be unchanged, and a
        silent index write on upload would couple the two pipelines.
        """
        patient = make_patient(name="Manual", suffix="0206")
        document = make_document(patient)
        response = retrieve(
            rag_client, patient, document, "paracetamol", top_k=5
        )
        assert response.status_code == 200
        assert response.json()["match_count"] == 0
        assert response.json()["indexed_chunks"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Retrieval
# ═══════════════════════════════════════════════════════════════════════════


class TestRetrieval:
    def test_returns_grounded_chunks_with_pages(
        self, rag_client, indexed_document
    ):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, "paracetamol dosage")
        assert response.status_code == 200
        body = response.json()

        assert body["match_count"] >= 1
        assert body["indexed_chunks"] >= 1
        assert body["document_id"] == str(document.id)
        assert body["grounded"] is True

        top = body["chunks"][0]
        assert top["source_page"] in (1, 2)
        assert "paracetamol" in top["text"].lower()
        assert 0.0 <= top["score"] <= 1.0
        assert top["chunk_id"].startswith(str(document.id))

    def test_medication_query_ranks_the_medication_page_first(
        self, rag_client, indexed_document
    ):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, "wound dressing care")
        assert response.status_code == 200
        top = response.json()["chunks"][0]
        assert "dressing" in top["text"].lower()

    def test_query_absent_from_document_returns_empty_success(
        self, rag_client, indexed_document
    ):
        """No mention is a valid answer, not an error."""
        patient, document, _ = indexed_document
        response = retrieve(
            rag_client, patient, document, "insulin sliding scale"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["match_count"] == 0
        assert body["chunks"] == []

    def test_source_page_is_null_not_guessed_for_unmarked_text(
        self, rag_client, make_patient, make_document
    ):
        patient = make_patient(name="Unmarked", suffix="0300")
        document = make_document(
            patient,
            extracted_text=(
                "Paracetamol 500 mg every six hours for pain relief. "
                "Continue as prescribed by the ward."
            ),
        )
        rag_client.post(index_url(document.id), json={"patient_id": str(patient.id)})
        response = retrieve(rag_client, patient, document, "paracetamol")
        assert response.status_code == 200
        chunks = response.json()["chunks"]
        assert chunks
        assert all(c["source_page"] is None for c in chunks)

    def test_response_never_echoes_the_query(self, rag_client, indexed_document):
        patient, document, _ = indexed_document
        needle = "a very distinctive search phrase that must not be echoed"
        response = retrieve(rag_client, patient, document, needle)
        assert response.status_code == 200
        assert needle not in response.text
        assert response.json()["query_length"] == len(needle)

    def test_top_k_within_schema_bounds_is_accepted(
        self, rag_client, indexed_document
    ):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, "care", top_k=100)
        assert response.status_code == 200
        assert response.json()["match_count"] <= 25

    def test_top_k_above_schema_max_is_rejected(
        self, rag_client, indexed_document
    ):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, "care", top_k=100000)
        assert response.status_code == 422

    def test_top_k_of_one_returns_a_single_chunk(
        self, rag_client, indexed_document
    ):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, "dressing", top_k=1)
        assert response.status_code == 200
        assert response.json()["match_count"] == 1

    def test_retrieval_before_indexing_is_an_empty_success(
        self, rag_client, make_patient, make_document
    ):
        patient = make_patient(name="Unindexed", suffix="0301")
        document = make_document(patient)
        response = retrieve(rag_client, patient, document, "paracetamol")
        assert response.status_code == 200
        assert response.json()["match_count"] == 0

    def test_response_reports_the_applied_score_floor(
        self, rag_client, indexed_document
    ):
        patient, document, _ = indexed_document
        body = retrieve(rag_client, patient, document, "paracetamol").json()
        assert body["min_score"] == pytest.approx(0.10)
        assert all(c["score"] >= body["min_score"] for c in body["chunks"])


# ═══════════════════════════════════════════════════════════════════════════
# Relevance floor
# ═══════════════════════════════════════════════════════════════════════════


class TestRelevanceFloor:
    """
    A vector store always returns its N nearest neighbours. These tests pin
    down that a query the document does not address yields NOTHING rather
    than the least-unrelated passage.
    """

    #: Queries that share no content word with the sample document.
    NO_OVERLAP_QUERIES = [
        "insulin sliding scale",
        "dialysis machine settings",
        "cataract surgery",
        "kidney stone lithotripsy",
        "pregnancy ultrasound",
        "eczema rash",
        "glaucoma drops",
        "chemotherapy infusion",
        "thyroid function",
        "asthma inhaler",
        "amoxicillin antibiotic",
        "appendectomy scar",
    ]

    @pytest.mark.parametrize("query", NO_OVERLAP_QUERIES)
    def test_unrelated_query_returns_nothing(self, rag_client, indexed_document, query):
        patient, document, _ = indexed_document
        body = retrieve(rag_client, patient, document, query).json()
        assert body["match_count"] == 0, f"{query!r} returned unrelated text"
        assert body["chunks"] == []

    def test_related_query_still_returns_results(
        self, rag_client, indexed_document
    ):
        """The floor must not be so aggressive that real matches vanish."""
        patient, document, _ = indexed_document
        for query in (
            "paracetamol dosage",
            "wound dressing",
            "physiotherapy",
            "follow up appointment",
            "pain relief",
        ):
            body = retrieve(rag_client, patient, document, query).json()
            assert body["match_count"] >= 1, f"{query!r} found nothing"

    def test_generic_term_overlap_is_returned_with_a_visible_score(
        self, rag_client, indexed_document
    ):
        """
        Documented limitation, pinned deliberately.

        A query sharing only a GENERIC word with the document ("medication")
        scores moderately and IS returned. For a bag-of-words model that is
        correct behaviour rather than a bug, and the score is exposed so the
        caller can judge it. This test exists so that anyone tightening the
        floor knows this case is intentional.
        """
        patient, document, _ = indexed_document
        body = retrieve(rag_client, patient, document, "seizure medication").json()
        assert body["match_count"] >= 1
        assert body["chunks"][0]["score"] >= body["min_score"]


# ═══════════════════════════════════════════════════════════════════════════
# Service-level limit clamping
# ═══════════════════════════════════════════════════════════════════════════


class TestTopKClamping:
    """`top_k` is bounded in the service even when it bypasses the schema."""

    def _service(self, rag_settings):
        from app.rag.retrieval import RagRetrievalService

        # No session needed: _resolve_top_k is pure configuration logic.
        return RagRetrievalService(None, settings=rag_settings)

    @pytest.mark.parametrize(
        "requested,expected",
        [
            (None, 5),      # default
            (1, 1),
            (5, 5),
            (25, 25),
            (26, 25),      # clamped to RAG_MAX_TOP_K
            (10_000, 25),  # clamped, not trusted
            (0, 1),        # never zero
            (-5, 1),
        ],
    )
    def test_resolve_top_k_clamps(self, rag_settings, requested, expected):
        assert self._service(rag_settings)._resolve_top_k(requested) == expected

    def test_non_numeric_top_k_falls_back_to_default(self, rag_settings):
        assert self._service(rag_settings)._resolve_top_k("abc") == 5


# ═══════════════════════════════════════════════════════════════════════════
# TENANCY
# ═══════════════════════════════════════════════════════════════════════════


class TestTenancy:
    def test_cannot_retrieve_another_patients_document(
        self, rag_client, indexed_document, make_patient
    ):
        patient, document, _ = indexed_document
        intruder = make_patient(name="Intruder", suffix="0400")
        response = retrieve(rag_client, intruder, document, "paracetamol")
        assert response.status_code == 404
        assert "paracetamol" not in response.text.lower()

    def test_cannot_retrieve_with_a_mismatched_patient_and_document(
        self, rag_client, make_patient, make_document
    ):
        first = make_patient(name="First", suffix="0401")
        second = make_patient(name="Second", suffix="0402")
        document = make_document(first)
        rag_client.post(index_url(document.id), json={"patient_id": str(first.id)})

        response = rag_client.post(
            RETRIEVE,
            json={
                "patient_id": str(second.id),
                "document_id": str(document.id),
                "query": "paracetamol",
            },
        )
        assert response.status_code == 404

    def test_two_patients_cannot_see_each_others_chunks(
        self, rag_client, make_patient, make_document
    ):
        alice = make_patient(name="Alice", suffix="0403")
        bob = make_patient(name="Bob", suffix="0404")
        alice_doc = make_document(
            alice,
            extracted_text="--- PAGE 1 ---\nAlice takes Warfarin 3 mg daily.",
            filename="alice.pdf",
        )
        bob_doc = make_document(
            bob,
            extracted_text="--- PAGE 1 ---\nBob takes Paracetamol 500 mg daily.",
            filename="bob.pdf",
        )
        for patient, document in ((alice, alice_doc), (bob, bob_doc)):
            rag_client.post(
                index_url(document.id), json={"patient_id": str(patient.id)}
            )

        alice_hits = retrieve(rag_client, alice, alice_doc, "warfarin").json()
        bob_hits = retrieve(rag_client, bob, bob_doc, "paracetamol").json()

        assert "warfarin" in alice_hits["chunks"][0]["text"].lower()
        assert "paracetamol" not in str(alice_hits).lower()
        assert "paracetamol" in bob_hits["chunks"][0]["text"].lower()
        assert "warfarin" not in str(bob_hits).lower()

    def test_missing_and_forbidden_are_indistinguishable(
        self, rag_client, indexed_document, make_patient
    ):
        """
        Identical 404 body for 'not yours' and 'does not exist', so the
        endpoint cannot be used to enumerate document IDs.
        """
        patient, document, _ = indexed_document
        intruder = make_patient(name="Probe", suffix="0405")

        forbidden = retrieve(rag_client, intruder, document, "x")
        missing = rag_client.post(
            RETRIEVE,
            json={
                "patient_id": str(intruder.id),
                "document_id": str(uuid.uuid4()),
                "query": "x",
            },
        )
        assert forbidden.status_code == missing.status_code == 404
        assert forbidden.json() == missing.json()


# ═══════════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════════


class TestValidation:
    @pytest.mark.parametrize("query", ["", "   ", "\n\t  "])
    def test_blank_query_is_rejected(self, rag_client, indexed_document, query):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, query)
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "payload",
        [
            {"query": "x"},
            {"patient_id": str(uuid.uuid4()), "query": "x"},
            {"document_id": str(uuid.uuid4()), "query": "x"},
            {
                "patient_id": "not-a-uuid",
                "document_id": str(uuid.uuid4()),
                "query": "x",
            },
        ],
    )
    def test_missing_or_malformed_fields_are_rejected(self, rag_client, payload):
        assert rag_client.post(RETRIEVE, json=payload).status_code == 422

    def test_unknown_fields_are_rejected(self, rag_client, indexed_document):
        """`extra="forbid"` stops a typo'd field silently disabling a filter."""
        patient, document, _ = indexed_document
        response = rag_client.post(
            RETRIEVE,
            json={
                "patient_id": str(patient.id),
                "document_id": str(document.id),
                "query": "x",
                "patient": "everything",
            },
        )
        assert response.status_code == 422

    def test_oversized_query_is_rejected(self, rag_client, indexed_document):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, "a" * 5000)
        assert response.status_code == 422

    def test_top_k_below_one_is_rejected(self, rag_client, indexed_document):
        patient, document, _ = indexed_document
        response = retrieve(rag_client, patient, document, "x", top_k=0)
        assert response.status_code == 422

    def test_malformed_json_does_not_crash(self, rag_client):
        response = rag_client.post(
            RETRIEVE,
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Healthcare safety boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestSafetyBoundary:
    @pytest.mark.parametrize(
        "field",
        ["answer", "summary", "recommendation", "interpretation", "diagnosis"],
    )
    def test_response_has_no_generated_content_fields(
        self, rag_client, indexed_document, field
    ):
        """
        Phase 3 returns source text only.  If a future change adds an
        answer-shaped field, this test is the thing that should fail first.
        """
        patient, document, _ = indexed_document
        body = retrieve(rag_client, patient, document, "paracetamol").json()
        assert field not in body
        assert field not in body["chunks"][0]

    def test_response_carries_a_safety_notice(
        self, rag_client, indexed_document
    ):
        patient, document, _ = indexed_document
        body = retrieve(rag_client, patient, document, "paracetamol").json()
        assert "not medical advice" in body["notice"].lower()

    def test_retrieved_text_is_verbatim_from_the_document(
        self, rag_client, indexed_document, make_document
    ):
        patient, document, _ = indexed_document
        body = retrieve(rag_client, patient, document, "paracetamol").json()
        source = make_document.SAMPLE_TEXT
        for chunk in body["chunks"]:
            # Every returned passage must appear literally in the source text.
            assert chunk["text"] in source

    def test_no_phi_in_logs(self, rag_client, indexed_document, caplog):
        """
        Query text and passages are PHI. Neither may reach the logs.
        """
        patient, document, _ = indexed_document
        needle = "distinctivephrasemustnotbe logged"
        with caplog.at_level(logging.DEBUG):
            retrieve(rag_client, patient, document, needle)
            retrieve(rag_client, patient, document, "paracetamol 500 mg")

        logged = caplog.text.lower()
        assert "distinctivephrasemustnotbe" not in logged
        assert "paracetamol" not in logged
        for record in caplog.records:
            rendered = str(record.getMessage()).lower()
            assert "paracetamol" not in rendered, record.getMessage()

    def test_rag_package_never_imports_an_llm(self):
        """
        Static check, so it cannot be defeated by a code path the tests
        happen not to exercise.

        Retrieval must stay retrieval. If `app/rag/` ever grows an import of
        the LLM layer, this fails — which is the point: a future answer-
        generating feature is a deliberate, reviewable change, not something
        that slips in as a side effect of a "small improvement".
        """
        import ast
        import pathlib

        import app.rag

        rag_root = pathlib.Path(app.rag.__file__).parent
        offenders = []
        for path in sorted(rag_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in {"app.llm", "llm"} or name.startswith(
                        ("groq", "google.generativeai", "openai", "anthropic")
                    ):
                        offenders.append(f"{path.name}: {name}")

        assert not offenders, f"app/rag/ must not import an LLM: {offenders}"

    def test_index_log_records_counts_not_content(
        self, rag_client, make_patient, make_document, caplog
    ):
        patient = make_patient(name="Logged", suffix="0500")
        document = make_document(patient)
        with caplog.at_level(logging.INFO):
            rag_client.post(
                index_url(document.id), json={"patient_id": str(patient.id)}
            )
        assert "Document indexed" in caplog.text
        assert "paracetamol" not in caplog.text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 non-regression
# ═══════════════════════════════════════════════════════════════════════════


class TestPhase2Intact:
    def test_phase2_routes_still_work(self, client, sample_patient):
        """Adding the RAG router must not disturb Phase 1/2 endpoints."""
        assert client.get("/api/v1/health").status_code == 200
        listed = client.get(
            f"/api/v1/patients/{sample_patient['id']}/discharge-documents"
        )
        assert listed.status_code == 200

    def test_rag_routes_are_registered_under_v1(self):
        from app.main import app

        paths = {route.path for route in app.routes}
        assert "/api/v1/rag/retrieve" in paths
        assert "/api/v1/rag/documents/{document_id}/index" in paths
