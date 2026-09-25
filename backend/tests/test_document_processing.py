"""
CareLoop AI — Phase 2: Document Processing Tests

Exercises PDF text extraction, scanned-page OCR dispatch, page limits, and
error translation.  OCR itself is mocked so Tesseract is never required.
"""
import io

import pytest
from PIL import Image

from app.core.exceptions import (
    CorruptedDocumentError,
    OCRUnavailableError,
    PageLimitExceededError,
)
from app.services.document_processing import DocumentProcessingService
from app.services.ocr import OCRService


@pytest.fixture
def service(phase2_settings):
    return DocumentProcessingService(phase2_settings)


class StubOCR:
    """Records OCR calls and returns canned text."""

    def __init__(
        self,
        text=(
            "DISCHARGE SUMMARY SCANNED PAGE\n"
            "Metformin 500 mg twice daily with meals for thirty days.\n"
            "Lisinopril 10 mg once daily. Cardiology follow-up 2026-10-15."
        ),
        error=None,
    ):
        self.text = text
        self.error = error
        self.calls = []

    def ocr_image_bytes(self, data):
        self.calls.append(data)
        if self.error is not None:
            raise self.error
        return self.text


@pytest.fixture
def stub_ocr():
    return StubOCR()


@pytest.fixture
def make_pdf():
    """Factory building a PDF from per-page text (None = image-only page)."""

    def _make(pages):
        import fitz

        doc = fitz.open()
        for text in pages:
            page = doc.new_page()
            if text:
                page.insert_text((50, 70), text, fontsize=9, fontname="cour")
        data = doc.tobytes()
        doc.close()
        return data

    return _make


# ── Text-layer PDFs ─────────────────────────────────────────────────────────

class TestTextLayerPdf:
    def test_extracts_embedded_text(self, service, make_pdf):
        data = make_pdf(["DISCHARGE SUMMARY Metformin 500 mg twice daily"])
        result = service.process_pdf(data)

        assert "Metformin" in result.text
        assert result.page_count == 1
        assert result.ocr_used is False
        assert result.ocr_page_count == 0

    def test_multi_page_text_is_joined(self, service, make_pdf):
        data = make_pdf(
            [
                "Page one of the discharge summary. Metformin 500 mg twice daily.",
                "Page two of the discharge summary. Lisinopril 10 mg once daily.",
            ]
        )
        result = service.process_pdf(data)

        assert result.page_count == 2
        assert "Metformin" in result.text
        assert "Lisinopril" in result.text

    def test_ocr_not_used_when_text_layer_present(
        self, phase2_settings, make_pdf, stub_ocr
    ):
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub_ocr)
        data = make_pdf(["Plenty of real text on this page to satisfy the minimum"])
        svc.process_pdf(data)
        assert stub_ocr.calls == []


# ── Scanned PDFs ────────────────────────────────────────────────────────────

class TestScannedPdf:
    def test_ocrs_page_without_text_layer(self, phase2_settings, make_pdf, stub_ocr):
        """A page with a too-thin text layer is treated as a scan."""
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub_ocr)
        data = make_pdf([""])  # empty page -> no text layer

        result = svc.process_pdf(data)

        assert len(stub_ocr.calls) == 1
        assert result.ocr_used is True
        assert result.ocr_page_count == 1
        assert "Metformin" in result.text

    def test_scanned_image_page_ocrs(self, service, scanned_pdf_bytes, stub_ocr):
        svc = DocumentProcessingService(service._settings, ocr_service=stub_ocr)
        result = svc.process_pdf(scanned_pdf_bytes)
        assert result.ocr_used is True
        assert "Metformin" in result.text

    def test_only_scanned_pages_are_ocrd(
        self, phase2_settings, make_pdf, stub_ocr
    ):
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub_ocr)
        data = make_pdf(
            [
                "This page has a substantial embedded text layer already present",
                "",
            ]
        )
        result = svc.process_pdf(data)

        # Only the blank second page needed OCR.
        assert len(stub_ocr.calls) == 1
        assert result.ocr_page_count == 1


# ── Error translation ───────────────────────────────────────────────────────

class TestErrorTranslation:
    def test_invalid_pdf_bytes_rejected(self, service):
        with pytest.raises(CorruptedDocumentError) as exc:
            service.process_pdf(b"%PDF-1.4 this is not really a pdf body")
        assert exc.value.status_code == 422

    def test_empty_pdf_rejected(self, service):
        with pytest.raises(CorruptedDocumentError):
            service.process_pdf(b"")

    def test_garbage_stream_rejected(self, service):
        with pytest.raises(CorruptedDocumentError):
            service.process_pdf(b"\x00\x01\x02\x03" * 50)

    def test_page_limit_enforced(self, phase2_settings, make_pdf):
        svc = DocumentProcessingService(
            phase2_settings.model_copy(update={"max_document_pages": 2})
        )
        data = make_pdf(["Page text one", "Page text two", "Page text three"])
        with pytest.raises(PageLimitExceededError) as exc:
            svc.process_pdf(data)
        assert exc.value.status_code == 400

    def test_missing_tesseract_on_scanned_pdf_is_503_not_422(
        self, phase2_settings, make_pdf
    ):
        """
        Regression: a missing OCR dependency is a server misconfiguration.

        It must surface as 503 (OCRUnavailableError), never as 422
        "corrupted document", which would wrongly blame the uploader.
        """
        stub = StubOCR(error=OCRUnavailableError("Tesseract is not installed."))
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub)
        data = make_pdf([""])  # forces the OCR path

        with pytest.raises(OCRUnavailableError) as exc:
            svc.process_pdf(data)
        assert exc.value.status_code == 503

    def test_missing_tesseract_on_image_is_503(self, phase2_settings, scan_png_bytes):
        stub = StubOCR(error=OCRUnavailableError("Tesseract is not installed."))
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub)
        with pytest.raises(OCRUnavailableError) as exc:
            svc.process_image(scan_png_bytes)
        assert exc.value.status_code == 503

    def test_unexpected_ocr_crash_becomes_corrupted(self, phase2_settings, make_pdf):
        stub = StubOCR(error=RuntimeError("unexpected library crash"))
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub)
        data = make_pdf([""])

        with pytest.raises(CorruptedDocumentError):
            svc.process_pdf(data)


# ── Image pipeline ──────────────────────────────────────────────────────────

class TestImagePipeline:
    def test_processes_png(self, phase2_settings, scan_png_bytes, stub_ocr):
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub_ocr)
        result = svc.process_image(scan_png_bytes)
        assert result.page_count == 1
        assert result.ocr_used is True

    def test_corrupt_image_rejected(self, service):
        with pytest.raises(CorruptedDocumentError):
            service.process_image(b"definitely not an image")

    def test_dispatch_selects_pdf_path(self, service, make_pdf):
        data = make_pdf(["DISCHARGE SUMMARY with real embedded text here"])
        result = service.process(
            data=data, content_type="application/pdf", filename="a.pdf"
        )
        assert result.ocr_used is False

    def test_dispatch_selects_image_path(
        self, phase2_settings, scan_png_bytes, stub_ocr
    ):
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub_ocr)
        result = svc.process(
            data=scan_png_bytes, content_type="image/png", filename="scan.png"
        )
        assert result.page_count == 1
        assert result.ocr_used is True

    def test_content_type_parameter_is_tolerated(
        self, phase2_settings, scan_png_bytes, stub_ocr
    ):
        svc = DocumentProcessingService(phase2_settings, ocr_service=stub_ocr)
        result = svc.process(
            data=scan_png_bytes,
            content_type="image/png; charset=binary",
            filename="scan.png",
        )
        assert result.page_count == 1
