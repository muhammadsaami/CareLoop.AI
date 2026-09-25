"""
CareLoop AI — Phase 2: Discharge Document API Tests

Exercises the HTTP contract end-to-end: status codes, response shape, and
error translation.  The LLM provider is a test double and uploads go to a
temporary directory, so no network or Tesseract install is required.
"""
import io
import uuid

import pytest
from PIL import Image

from app.core.exceptions import (
    OCRUnavailableError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
)
from tests.conftest import FakeProvider


DISCHARGE_PAGE = (
    "DISCHARGE SUMMARY - General Hospital\n"
    "Patient: Jane Doe.  Discharge date: 2026-09-20.\n"
    "Medications: Metformin 500 mg twice daily with meals for 30 days.\n"
    "Lisinopril 10 mg once daily for 30 days.\n"
    "Follow-up: Cardiology on 2026-10-15 at Main Clinic, Room 204.\n"
    "Warning: Fever above 101 F - contact your care team immediately.\n"
)


def make_pdf(pages=(DISCHARGE_PAGE,)):
    import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((50, 70), text, fontsize=9, fontname="cour")
    data = doc.tobytes()
    doc.close()
    return data


def make_png():
    buf = io.BytesIO()
    Image.new("RGB", (800, 300), color="white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def patient_id(client):
    """Create a patient through the API and return its id."""
    response = client.post(
        "/api/v1/patients",
        json={
            "name": "Jane Doe",
            "contact_number": "+1-555-0199",
            "caregiver_contact": "+1-555-0188",
            "discharge_date": "2026-09-20",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, patient_id, data, filename="summary.pdf", ctype="application/pdf"):
    return client.post(
        "/api/v1/discharge-documents",
        data={"patient_id": patient_id},
        files={"file": (filename, data, ctype)},
    )


# ── Happy path ──────────────────────────────────────────────────────────────

class TestUploadEndpoint:
    def test_upload_returns_201_with_counts(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        response = upload(dc, patient_id, make_pdf())

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["counts"]["medications"] == 2
        assert body["counts"]["appointments"] == 1
        assert body["counts"]["warning_symptoms"] == 1
        assert body["processing_status"] == "completed"
        assert body["ocr_status"] == "not_required"
        assert body["page_count"] == 1
        assert len(body["created_medication_ids"]) == 2

    def test_response_never_contains_document_text(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        body = upload(dc, patient_id, make_pdf()).json()
        assert "extracted_text" not in body
        assert "file_path" not in body
        assert "sha256_hash" not in body

    def test_creating_records_are_queryable_afterwards(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        upload(dc, patient_id, make_pdf())

        meds = client.get(f"/api/v1/patients/{patient_id}/medications")
        assert meds.status_code == 200
        assert len(meds.json()) == 2
        assert {m["name"] for m in meds.json()} == {"Metformin", "Lisinopril"}

        appts = client.get(f"/api/v1/patients/{patient_id}/appointments")
        assert appts.status_code == 200
        assert len(appts.json()) == 1

    def test_provider_receives_document_text(
        self, document_client, client, patient_id
    ):
        provider = FakeProvider()
        dc = document_client(provider)
        upload(dc, patient_id, make_pdf())

        assert len(provider.calls) == 1
        call = provider.calls[0]
        assert "Metformin" in call["user_prompt"]
        assert call["system_prompt"]
        assert "medications" in call["json_schema"]["properties"]

    def test_prompt_forbids_inventing_clinical_content(
        self, document_client, client, patient_id
    ):
        provider = FakeProvider()
        dc = document_client(provider)
        upload(dc, patient_id, make_pdf())

        system = provider.calls[0]["system_prompt"].lower()
        # The safety instructions must actually reach the model.
        assert "verbatim" in system or "exactly" in system
        assert "diagnos" in system or "prescrib" in system


# ── Validation errors ───────────────────────────────────────────────────────

class TestUploadValidation:
    def test_unsupported_file_type_returns_400(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        response = upload(
            dc, patient_id, b"plain text", filename="notes.txt", ctype="text/plain"
        )
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_unknown_patient_returns_404(
        self, document_client, fake_provider, client
    ):
        dc = document_client(fake_provider)
        response = upload(dc, str(uuid.uuid4()), make_pdf())
        assert response.status_code == 404

    def test_duplicate_upload_returns_409(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        data = make_pdf()
        assert upload(dc, patient_id, data).status_code == 201

        response = upload(dc, patient_id, data, filename="copy.pdf")
        assert response.status_code == 409
        assert "already" in response.json()["detail"].lower()

    def test_oversized_upload_returns_413(
        self, document_client, fake_provider, client, patient_id, monkeypatch
    ):
        import app.services.storage as storage_module

        original_init = storage_module.DocumentStorageService.__init__

        def small_init(self, settings=None):
            from app.core.config import get_settings

            original_init(
                self,
                (settings or get_settings()).model_copy(
                    update={"max_upload_size_mb": 0}
                ),
            )

        monkeypatch.setattr(
            storage_module.DocumentStorageService, "__init__", small_init
        )

        dc = document_client(fake_provider)
        response = upload(dc, patient_id, make_pdf())
        assert response.status_code == 413

    def test_corrupt_pdf_returns_422(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        response = upload(dc, patient_id, b"%PDF-1.4 definitely not a real pdf")
        assert response.status_code == 422

    def test_invalid_patient_id_format_returns_422(
        self, document_client, fake_provider, client
    ):
        dc = document_client(fake_provider)
        response = upload(dc, "not-a-uuid", make_pdf())
        assert response.status_code == 422

    def test_missing_file_returns_422(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        response = dc.post(
            "/api/v1/discharge-documents", data={"patient_id": patient_id}
        )
        assert response.status_code == 422


# ── Provider and OCR failures ───────────────────────────────────────────────

class TestProviderErrors:
    def test_provider_timeout_returns_504(
        self, document_client, client, patient_id
    ):
        provider = FakeProvider(error=ProviderTimeoutError("timed out"))
        dc = document_client(provider)
        response = upload(dc, patient_id, make_pdf())
        assert response.status_code == 504

    def test_unconfigured_provider_returns_503(
        self, document_client, client, patient_id
    ):
        provider = FakeProvider()
        provider.is_configured = lambda: False
        dc = document_client(provider)
        response = upload(dc, patient_id, make_pdf())
        assert response.status_code == 503

    def test_ocr_unavailable_returns_503(
        self, document_client, fake_provider, client, patient_id, monkeypatch
    ):
        """
        A missing Tesseract install is a server misconfiguration, not a
        client error, so it must be 503 and never 422.
        """
        import app.services.document_processing as dp

        def unavailable(self, data):
            raise OCRUnavailableError("Tesseract is not installed.")

        monkeypatch.setattr(dp.OCRService, "ocr_image_bytes", unavailable)

        dc = document_client(fake_provider)
        response = upload(dc, patient_id, make_png(), filename="scan.png", ctype="image/png")
        assert response.status_code == 503
        assert "tesseract" in response.json()["detail"].lower()

    def test_provider_error_does_not_leak_api_key(
        self, document_client, client, patient_id
    ):
        """
        Even if a provider ever interpolates vendor text into a message,
        a key-shaped value must not reach the client.
        """
        leaked = "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
        provider = FakeProvider(
            error=ProviderNotConfiguredError(f"key {leaked} rejected")
        )
        dc = document_client(provider)
        response = upload(dc, patient_id, make_pdf())

        assert leaked not in response.text
        assert "[redacted]" in response.json()["detail"]

    def test_configured_api_key_is_redacted(
        self, document_client, client, patient_id
    ):
        """A key present in configuration is masked by exact match."""
        from app.core.config import get_settings
        from app.core.redaction import redact_secrets

        settings = get_settings().model_copy(
            update={"groq_api_key": "super-secret-key-value-1234"}
        )
        out = redact_secrets(
            "request failed with super-secret-key-value-1234", settings=settings
        )
        assert "super-secret-key-value-1234" not in out


# ── Retrieval endpoints ─────────────────────────────────────────────────────

class TestRetrieval:
    def test_get_document_by_id(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        created = upload(dc, patient_id, make_pdf()).json()

        response = client.get(
            f"/api/v1/discharge-documents/{created['document_id']}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == created["document_id"]
        assert body["page_count"] == 1

    def test_get_document_includes_extraction_audit(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        created = upload(dc, patient_id, make_pdf()).json()

        body = client.get(
            f"/api/v1/discharge-documents/{created['document_id']}"
        ).json()
        assert "extraction_runs" in body
        assert len(body["extraction_runs"]) == 1
        assert body["extraction_runs"][0]["status"] == "completed"

    def test_get_unknown_document_returns_404(self, client):
        response = client.get(f"/api/v1/discharge-documents/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_get_document_with_invalid_uuid_returns_422(self, client):
        response = client.get("/api/v1/discharge-documents/not-a-uuid")
        assert response.status_code == 422

    def test_list_documents_for_patient(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        upload(dc, patient_id, make_pdf())

        response = client.get(f"/api/v1/patients/{patient_id}/discharge-documents")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_list_documents_empty_for_new_patient(self, client, patient_id):
        response = client.get(f"/api/v1/patients/{patient_id}/discharge-documents")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_documents_unknown_patient_returns_404(self, client):
        response = client.get(
            f"/api/v1/patients/{uuid.uuid4()}/discharge-documents"
        )
        assert response.status_code == 404


# ── Reprocess ───────────────────────────────────────────────────────────────

class TestReprocess:
    def test_reprocess_runs_extraction(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        created = upload(dc, patient_id, make_pdf()).json()

        response = client.post(
            f"/api/v1/discharge-documents/{created['document_id']}/reprocess"
        )
        assert response.status_code == 200, response.text
        assert response.json()["counts"]["medications"] == 2

    def test_reprocess_unknown_document_returns_404(self, client):
        response = client.post(
            f"/api/v1/discharge-documents/{uuid.uuid4()}/reprocess"
        )
        assert response.status_code == 404

    def test_reprocess_records_another_audit_run(
        self, document_client, fake_provider, client, patient_id
    ):
        dc = document_client(fake_provider)
        created = upload(dc, patient_id, make_pdf()).json()
        client.post(f"/api/v1/discharge-documents/{created['document_id']}/reprocess")

        body = client.get(
            f"/api/v1/discharge-documents/{created['document_id']}"
        ).json()
        assert len(body["extraction_runs"]) == 2


# ── OpenAPI contract ────────────────────────────────────────────────────────

class TestOpenApi:
    def test_phase2_routes_are_documented(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        assert "/api/v1/discharge-documents" in paths
        assert "/api/v1/discharge-documents/{document_id}" in paths
        assert "/api/v1/discharge-documents/{document_id}/reprocess" in paths
        assert "/api/v1/patients/{patient_id}/discharge-documents" in paths

    def test_upload_declares_multipart(self, client):
        spec = client.get("/openapi.json").json()
        body = spec["paths"]["/api/v1/discharge-documents"]["post"]["requestBody"]
        assert "multipart/form-data" in body["content"]

    def test_health_still_works(self, client):
        """Phase 2 must not regress Phase 1."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
