"""
CareLoop AI — Phase 2: Discharge Document Pipeline & API Tests

End-to-end coverage of the ingestion pipeline with the LLM provider
replaced by a test double: no network calls and no Tesseract requirement.
"""
import io
import uuid

import pytest
from PIL import Image

from app.core.exceptions import (
    CorruptedDocumentError,
    DuplicateDocumentError,
    ProviderTimeoutError,
    UnsupportedFileTypeError,
)
from app.models.discharge_document import (
    ExtractionStatus,
    ProcessingStatus,
)
from app.models.patient import Patient
from app.services import document_processing as document_processing_module
from app.services.discharge_document import DischargeDocumentService
from app.services.document_processing import DocumentProcessingService
from tests.conftest import FakeProvider


# ── Helpers ─────────────────────────────────────────────────────────────────

def make_pdf(pages):
    import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((50, 70), text, fontsize=9, fontname="cour")
    data = doc.tobytes()
    doc.close()
    return data


DISCHARGE_PAGE = (
    "DISCHARGE SUMMARY - General Hospital\n"
    "Patient: Jane Doe.  Discharge date: 2026-09-20.\n"
    "Medications: Metformin 500 mg twice daily with meals for 30 days.\n"
    "Lisinopril 10 mg once daily for 30 days.\n"
    "Follow-up: Cardiology on 2026-10-15 at Main Clinic, Room 204.\n"
    "Warning: Fever above 101 F - contact your care team immediately.\n"
)


@pytest.fixture
def patient(db_session):
    obj = Patient(
        id=uuid.uuid4(),
        name="Jane Doe",
        contact_number="+1-555-0199",
        caregiver_contact="+1-555-0188",
        discharge_date="2026-09-20",
    )
    db_session.add(obj)
    db_session.commit()
    db_session.refresh(obj)
    return obj


@pytest.fixture
def service(db_session, phase2_settings, fake_provider):
    return DischargeDocumentService(
        db_session, settings=phase2_settings, llm_provider=fake_provider
    )


# ── Happy path ──────────────────────────────────────────────────────────────

class TestUploadHappyPath:
    def test_extracts_and_persists_everything(self, service, patient, db_session):
        result = service.upload_and_process(
            patient_id=patient.id,
            filename="discharge-summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )

        assert result.counts.medications == 2
        assert result.counts.appointments == 1
        assert result.counts.warning_symptoms == 1
        assert result.processing_status == ProcessingStatus.COMPLETED.value
        assert result.ocr_status == "not_required"
        assert result.page_count == 1

        assert len(result.created_medication_ids) == 2
        assert len(result.created_appointment_ids) == 1
        assert len(result.created_warning_symptom_ids) == 1

    def test_medication_values_are_transcribed_verbatim(
        self, service, patient, db_session
    ):
        from app.models.medication import Medication

        result = service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )

        rows = (
            db_session.query(Medication)
            .filter(Medication.id.in_(result.created_medication_ids))
            .all()
        )
        by_name = {r.name: r for r in rows}
        assert "Metformin" in by_name
        assert by_name["Metformin"].dosage == "500 mg"
        assert by_name["Metformin"].frequency == "twice daily with meals"

    def test_warning_symptom_is_stored_pending_review(
        self, service, patient, db_session
    ):
        from app.models.warning_symptom import WarningSymptom

        result = service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )

        row = (
            db_session.query(WarningSymptom)
            .filter(WarningSymptom.id.in_(result.created_warning_symptom_ids))
            .one()
        )
        # Phase 1's WarningSymptom has no needs_review column, so the
        # unverified state is carried in the description instead.
        assert "Fever" in row.description
        assert "REQUIRES REVIEW" in row.description
        assert "extracted from document" in row.description

    def test_warning_symptom_keeps_source_text_for_verification(
        self, service, patient, db_session
    ):
        from app.models.warning_symptom import WarningSymptom

        result = service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )
        row = (
            db_session.query(WarningSymptom)
            .filter(WarningSymptom.id.in_(result.created_warning_symptom_ids))
            .one()
        )
        # A clinician must be able to check the claim against the document.
        assert "Source text" in row.description
        assert "Source page" in row.description

    def test_document_row_records_metadata(
        self, service, patient, db_session
    ):
        from app.models.discharge_document import DischargeDocument

        result = service.upload_and_process(
            patient_id=patient.id,
            filename="My Summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )

        doc = db_session.get(DischargeDocument, uuid.UUID(result.document_id))
        assert doc.processing_status == ProcessingStatus.COMPLETED
        assert doc.page_count == 1
        assert len(doc.sha256_hash) == 64
        assert "Metformin" in doc.extracted_text

    def test_extraction_flagged_needs_review_when_symptoms_present(
        self, service, patient, db_session
    ):
        """
        Warning symptoms are never machine-verified, so the document is
        flagged for review rather than reported as fully completed.
        """
        from app.models.discharge_document import DischargeDocument

        result = service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )
        doc = db_session.get(DischargeDocument, uuid.UUID(result.document_id))
        assert doc.extraction_status == ExtractionStatus.NEEDS_REVIEW
        assert result.extraction_status == ExtractionStatus.NEEDS_REVIEW.value
        assert result.counts.needs_review >= 1

    def test_clean_extraction_without_symptoms_is_completed(
        self, db_session, phase2_settings, patient
    ):
        from app.models.discharge_document import DischargeDocument

        provider = FakeProvider(
            payload={
                "medications": [
                    {
                        "medication_name": "Metformin",
                        "dosage": "500 mg",
                        "frequency": "twice daily",
                    }
                ]
            }
        )
        svc = DischargeDocumentService(
            db_session, settings=phase2_settings, llm_provider=provider
        )
        result = svc.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )
        doc = db_session.get(DischargeDocument, uuid.UUID(result.document_id))
        assert doc.extraction_status == ExtractionStatus.COMPLETED
        assert result.counts.needs_review == 0

    def test_original_filename_is_preserved_for_display(
        self, service, patient, db_session
    ):
        from app.models.discharge_document import DischargeDocument

        result = service.upload_and_process(
            patient_id=patient.id,
            filename="My Discharge Summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )
        doc = db_session.get(DischargeDocument, uuid.UUID(result.document_id))
        assert doc.original_filename == "My Discharge Summary.pdf"
        # The stored name is server-generated.
        assert doc.stored_filename != "My Discharge Summary.pdf"
        assert "jane" not in doc.stored_filename.lower()

    def test_response_omits_full_document_text(self, service, patient):
        """The API envelope returns counts and ids, never the document text."""
        result = service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )
        payload = result.model_dump()

        assert "extracted_text" not in payload
        assert "sha256_hash" not in payload
        assert "file_path" not in payload
        assert set(payload) >= {
            "document_id",
            "patient_id",
            "provider",
            "model",
            "processing_status",
            "counts",
        }


# ── Validation and safety rails ─────────────────────────────────────────────

class TestUploadValidation:
    def test_unknown_patient_rejected(self, service, db_session):
        from app.core.exceptions import DocumentNotFoundError

        with pytest.raises(DocumentNotFoundError):
            service.upload_and_process(
                patient_id=uuid.uuid4(),
                filename="summary.pdf",
                content_type="application/pdf",
                data=make_pdf([DISCHARGE_PAGE]),
            )

    def test_unsupported_type_rejected(self, service, patient):
        with pytest.raises(UnsupportedFileTypeError):
            service.upload_and_process(
                patient_id=patient.id,
                filename="notes.txt",
                content_type="text/plain",
                data=b"hello",
            )

    def test_rejected_upload_leaves_no_database_row(
        self, service, patient, db_session
    ):
        from app.models.discharge_document import DischargeDocument

        with pytest.raises(UnsupportedFileTypeError):
            service.upload_and_process(
                patient_id=patient.id,
                filename="notes.txt",
                content_type="text/plain",
                data=b"hello",
            )
        assert db_session.query(DischargeDocument).count() == 0

    def test_rejected_upload_leaves_no_file_on_disk(
        self, service, patient, phase2_settings
    ):
        from pathlib import Path

        root = Path(phase2_settings.document_storage_path)
        before = list(root.iterdir())
        with pytest.raises(UnsupportedFileTypeError):
            service.upload_and_process(
                patient_id=patient.id,
                filename="notes.txt",
                content_type="text/plain",
                data=b"hello",
            )
        assert list(root.iterdir()) == before

    def test_corrupt_pdf_marked_failed(self, service, patient, db_session):
        from app.models.discharge_document import DischargeDocument

        with pytest.raises(CorruptedDocumentError):
            service.upload_and_process(
                patient_id=patient.id,
                filename="summary.pdf",
                content_type="application/pdf",
                data=b"%PDF-1.4 not a real pdf",
            )

        doc = db_session.query(DischargeDocument).one()
        # The row is retained for audit, marked failed with a reason.
        assert doc.processing_status == ProcessingStatus.FAILED
        assert doc.error_message


# ── Duplicates ──────────────────────────────────────────────────────────────

class TestDuplicateUploads:
    def test_same_bytes_same_patient_rejected(self, service, patient):
        data = make_pdf([DISCHARGE_PAGE])
        service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=data,
        )
        with pytest.raises(DuplicateDocumentError):
            service.upload_and_process(
                patient_id=patient.id,
                filename="summary-copy.pdf",
                content_type="application/pdf",
                data=data,
            )

    def test_duplicate_does_not_create_a_second_row(
        self, service, patient, db_session
    ):
        from app.models.discharge_document import DischargeDocument

        data = make_pdf([DISCHARGE_PAGE])
        service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=data,
        )
        with pytest.raises(DuplicateDocumentError):
            service.upload_and_process(
                patient_id=patient.id,
                filename="summary-copy.pdf",
                content_type="application/pdf",
                data=data,
            )
        assert db_session.query(DischargeDocument).count() == 1

    def test_duplicate_does_not_leave_an_orphan_file(
        self, service, patient, phase2_settings
    ):
        from pathlib import Path

        data = make_pdf([DISCHARGE_PAGE])
        service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=data,
        )
        root = Path(phase2_settings.document_storage_path)
        before = sorted(p.name for p in root.iterdir())

        with pytest.raises(DuplicateDocumentError):
            service.upload_and_process(
                patient_id=patient.id,
                filename="summary-copy.pdf",
                content_type="application/pdf",
                data=data,
            )
        after = sorted(p.name for p in root.iterdir())
        assert before == after

    def test_same_bytes_different_patient_allowed(self, service, patient, db_session):
        other = Patient(
            id=uuid.uuid4(),
            name="John Roe",
            contact_number="+1-555-0177",
            caregiver_contact="+1-555-0166",
            discharge_date="2026-09-18",
        )
        db_session.add(other)
        db_session.commit()

        data = make_pdf([DISCHARGE_PAGE])
        service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=data,
        )
        service.upload_and_process(
            patient_id=other.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=data,
        )

        from app.models.discharge_document import DischargeDocument

        assert db_session.query(DischargeDocument).count() == 2


# ── Provider failures ───────────────────────────────────────────────────────

class TestProviderFailures:
    def test_timeout_is_recorded_and_surfaced(
        self, db_session, phase2_settings, patient
    ):
        from app.models.discharge_document import DischargeDocument

        provider = FakeProvider(error=ProviderTimeoutError("Provider timed out."))
        svc = DischargeDocumentService(
            db_session, settings=phase2_settings, llm_provider=provider
        )

        with pytest.raises(ProviderTimeoutError):
            svc.upload_and_process(
                patient_id=patient.id,
                filename="summary.pdf",
                content_type="application/pdf",
                data=make_pdf([DISCHARGE_PAGE]),
            )

        doc = db_session.query(DischargeDocument).one()
        assert doc.extraction_status == ExtractionStatus.FAILED

    def test_failed_extraction_creates_no_phase1_records(
        self, db_session, phase2_settings, patient
    ):
        from app.models.medication import Medication

        provider = FakeProvider(error=ProviderTimeoutError("Provider timed out."))
        svc = DischargeDocumentService(
            db_session, settings=phase2_settings, llm_provider=provider
        )
        with pytest.raises(ProviderTimeoutError):
            svc.upload_and_process(
                patient_id=patient.id,
                filename="summary.pdf",
                content_type="application/pdf",
                data=make_pdf([DISCHARGE_PAGE]),
            )
        assert db_session.query(Medication).count() == 0

    def test_extraction_can_be_skipped(self, service, patient, db_session):
        from app.models.medication import Medication

        result = service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
            run_extraction=False,
        )
        assert result.provider == "skipped"
        assert result.counts.medications == 0
        assert db_session.query(Medication).count() == 0

    def test_extraction_run_is_recorded_for_audit(
        self, service, patient, db_session
    ):
        from app.models.extraction_run import ExtractionRun, ExtractionRunStatus

        service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )

        run = db_session.query(ExtractionRun).one()
        assert run.status == ExtractionRunStatus.COMPLETED
        assert run.provider == "fake"
        assert run.completed_at is not None


# ── Queries ─────────────────────────────────────────────────────────────────

class TestQueries:
    def test_list_for_patient_returns_only_their_documents(self, service, patient, db_session):
        other = Patient(
            id=uuid.uuid4(),
            name="John Roe",
            contact_number="+1-555-0177",
            caregiver_contact="+1-555-0166",
            discharge_date="2026-09-18",
        )
        db_session.add(other)
        db_session.commit()

        service.upload_and_process(
            patient_id=patient.id,
            filename="mine.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
        )
        service.upload_and_process(
            patient_id=other.id,
            filename="theirs.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE + " Extra unique content line here."]),
        )

        mine = service.list_for_patient(patient.id)
        assert len(mine) == 1
        assert mine[0].patient_id == patient.id

    def test_list_is_empty_for_patient_without_documents(self, service, patient):
        assert service.list_for_patient(patient.id) == []

    def test_get_missing_document_raises(self, service):
        from app.core.exceptions import DocumentNotFoundError

        with pytest.raises(DocumentNotFoundError):
            service.get_document(uuid.uuid4())

    def test_reprocess_reruns_extraction(self, service, patient, db_session):
        from app.models.medication import Medication

        first = service.upload_and_process(
            patient_id=patient.id,
            filename="summary.pdf",
            content_type="application/pdf",
            data=make_pdf([DISCHARGE_PAGE]),
            run_extraction=False,
        )
        assert db_session.query(Medication).count() == 0

        result = service.reprocess(uuid.UUID(first.document_id))
        assert result.counts.medications == 2
        assert db_session.query(Medication).count() == 2


# ── Image uploads ───────────────────────────────────────────────────────────

class TestImageUpload:
    @pytest.fixture
    def scan_png(self):
        buf = io.BytesIO()
        img = Image.new("RGB", (1000, 400), color="white")
        from PIL import ImageDraw

        d = ImageDraw.Draw(img)
        d.text((20, 40), "DISCHARGE SUMMARY", fill="black")
        d.text((20, 100), "Metformin 500 mg twice daily with meals for 30 days", fill="black")
        buf2 = io.BytesIO()
        img.save(buf2, format="PNG")
        return buf2.getvalue()

    def test_png_upload_uses_ocr(
        self, db_session, phase2_settings, patient, scan_png, monkeypatch
    ):
        monkeypatch.setattr(
            document_processing_module.OCRService,
            "ocr_image_bytes",
            lambda self, data: (
                "DISCHARGE SUMMARY\n"
                "Metformin 500 mg twice daily with meals for 30 days.\n"
                "Lisinopril 10 mg once daily for 30 days.\n"
                "Cardiology follow up on 2026-10-15 at Main Clinic Room 204.\n"
                "Warning fever above 101 F contact your care team immediately.\n"
            ),
        )

        provider = FakeProvider()
        svc = DischargeDocumentService(
            db_session, settings=phase2_settings, llm_provider=provider
        )
        result = svc.upload_and_process(
            patient_id=patient.id,
            filename="scan.png",
            content_type="image/png",
            data=scan_png,
        )

        assert result.ocr_status == "completed"
        assert result.counts.medications == 2
        assert result.counts.appointments == 1

    def test_image_without_tesseract_returns_503(
        self, db_session, phase2_settings, patient, scan_png, monkeypatch
    ):
        """A missing OCR dependency must be reported as 503, not 422."""
        from app.core.exceptions import OCRUnavailableError

        def unavailable(self, data):
            raise OCRUnavailableError("Tesseract is not installed.")

        monkeypatch.setattr(
            document_processing_module.OCRService, "ocr_image_bytes", unavailable
        )

        svc = DischargeDocumentService(
            db_session, settings=phase2_settings, llm_provider=FakeProvider()
        )
        with pytest.raises(OCRUnavailableError) as exc:
            svc.upload_and_process(
                patient_id=patient.id,
                filename="scan.png",
                content_type="image/png",
                data=scan_png,
            )
        assert exc.value.status_code == 503
