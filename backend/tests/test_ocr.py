"""
CareLoop AI — Phase 2: OCR Service Tests

Tesseract is never invoked: `pytesseract` is patched, so these tests run on
any machine regardless of whether Tesseract is installed.  They verify the
*contract* (availability detection, error translation, decoding), not
Tesseract's recognition accuracy.
"""
import io

import pytest
from PIL import Image

from app.core.exceptions import (
    CorruptedDocumentError,
    OCRFailedError,
    OCRUnavailableError,
)
from app.services.ocr import OCRService


@pytest.fixture
def service(phase2_settings):
    return OCRService(phase2_settings)


def png_bytes(size=(40, 20), color="white"):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def tesseract_present(monkeypatch):
    """Simulate a working Tesseract installation."""

    def fake_version(*args, **kwargs):
        return "5.3.0"

    monkeypatch.setattr("pytesseract.get_tesseract_version", fake_version)


@pytest.fixture
def tesseract_missing(monkeypatch):
    """Simulate Tesseract not being installed / not on PATH."""

    def boom(*args, **kwargs):
        raise FileNotFoundError(
            "tesseract.exe is not installed or is not on your PATH."
        )

    monkeypatch.setattr("pytesseract.get_tesseract_version", boom)


# ── Availability ────────────────────────────────────────────────────────────

class TestAvailability:
    def test_available_when_executable_runs(self, service, tesseract_present):
        assert service.is_available() is True

    def test_unavailable_when_missing(self, service, tesseract_missing):
        assert service.is_available() is False

    def test_ensure_available_passes(self, service, tesseract_present):
        assert service.ensure_available() is None

    def test_ensure_available_raises_actionable_error(
        self, service, tesseract_missing
    ):
        with pytest.raises(OCRUnavailableError) as exc:
            service.ensure_available()

        # The message must tell an operator how to fix it.
        assert "TESSERACT_CMD" in str(exc.value)
        assert exc.value.status_code == 503

    def test_error_message_differs_when_path_configured(
        self, phase2_settings, tesseract_missing, monkeypatch
    ):
        configured = phase2_settings.model_copy(
            update={"tesseract_cmd": "C:/fake/tesseract.exe"}
        )
        svc = OCRService(configured)
        with pytest.raises(OCRUnavailableError) as exc:
            svc.ensure_available()
        assert "could not be run" in str(exc.value)


# ── OCR execution ───────────────────────────────────────────────────────────

class TestRunOCR:
    def test_returns_recognised_text(self, service, tesseract_present, monkeypatch):
        monkeypatch.setattr(
            "pytesseract.image_to_string", lambda *a, **k: "DISCHARGE SUMMARY"
        )
        image = Image.new("RGB", (10, 10))
        assert service.run_ocr(image) == "DISCHARGE SUMMARY"

    def test_uses_configured_language_and_config(
        self, phase2_settings, tesseract_present, monkeypatch
    ):
        captured = {}

        def fake_ocr(image, lang=None, config=None):
            captured["lang"] = lang
            captured["config"] = config
            return "text"

        monkeypatch.setattr("pytesseract.image_to_string", fake_ocr)
        svc = OCRService(phase2_settings.model_copy(update={"ocr_language": "spa"}))
        svc.run_ocr(Image.new("RGB", (10, 10)))

        assert captured["lang"] == "spa"
        assert "psm 6" in captured["config"]

    def test_translates_ocr_crash_to_domain_error(
        self, service, tesseract_present, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError("tesseract crashed")

        monkeypatch.setattr("pytesseract.image_to_string", boom)

        with pytest.raises(OCRFailedError) as exc:
            service.run_ocr(Image.new("RGB", (10, 10)))
        assert exc.value.status_code == 422

    def test_missing_tesseract_surfaces_as_unavailable(
        self, service, tesseract_missing
    ):
        """A missing binary must not be reported as an OCR failure."""
        with pytest.raises(OCRUnavailableError):
            service.run_ocr(Image.new("RGB", (10, 10)))


# ── Image decoding ──────────────────────────────────────────────────────────

class TestImageDecoding:
    def test_decodes_valid_png(self, service):
        image = service.load_image(png_bytes())
        assert image.size == (40, 20)

    def test_decodes_valid_jpeg(self, service):
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="JPEG")
        image = service.load_image(buf.getvalue())
        assert image.size == (10, 10)

    def test_rejects_non_image_bytes(self, service):
        with pytest.raises(CorruptedDocumentError) as exc:
            service.load_image(b"this is definitely not an image")
        assert exc.value.status_code == 422

    def test_rejects_empty_bytes(self, service):
        with pytest.raises(CorruptedDocumentError):
            service.load_image(b"")

    def test_rejects_truncated_png(self, service):
        """Truncated images must fail loudly rather than decode partially."""
        truncated = png_bytes()[: len(png_bytes()) // 2]
        with pytest.raises(CorruptedDocumentError):
            service.load_image(truncated)

    def test_truncated_images_are_not_tolerated(self):
        """Pillow strict mode is explicitly enforced by this module."""
        from PIL import ImageFile

        import app.services.ocr  # noqa: F401 - triggers module import

        assert ImageFile.LOAD_TRUNCATED_IMAGES is False


# ── Combined path ───────────────────────────────────────────────────────────

class TestOcrImageBytes:
    def test_decode_then_ocr(self, service, tesseract_present, monkeypatch):
        monkeypatch.setattr(
            "pytesseract.image_to_string", lambda *a, **k: "Metformin 500 mg"
        )
        assert service.ocr_image_bytes(png_bytes()) == "Metformin 500 mg"

    def test_corrupt_bytes_fail_before_ocr(
        self, service, tesseract_present, monkeypatch
    ):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("OCR must not run on undecodable bytes")

        monkeypatch.setattr("pytesseract.image_to_string", fail_if_called)
        with pytest.raises(CorruptedDocumentError):
            service.ocr_image_bytes(b"not an image at all")
