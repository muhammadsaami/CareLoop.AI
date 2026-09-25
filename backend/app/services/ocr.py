"""
CareLoop AI — Tesseract OCR Service (Phase 2)

Wraps pytesseract so that:
  - the executable path comes from configuration (TESSERACT_CMD), never
    from a hardcoded machine-specific location
  - a missing/unconfigured Tesseract produces a clear ConfigurationError
    instead of an opaque crash
  - corrupt images are reported as a controlled domain error
  - OCR failures are wrapped and never leak a stack trace to callers

This module performs optical character recognition only.  It does not
interpret, summarise, or modify the text it reads.
"""
from __future__ import annotations

import io
from typing import Optional

import pytesseract
from PIL import Image, ImageFile, UnidentifiedImageError

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CorruptedDocumentError,
    OCRFailedError,
    OCRUnavailableError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# A partially decoded clinical document is worse than a clean failure: it
# silently loses medication names and dosages.  Enforce Pillow's strict mode
# here so no other import can relax it into best-effort decoding.
ImageFile.LOAD_TRUNCATED_IMAGES = False

# OCR segmentation defaults.  PSM 6 = "assume a single uniform block of text",
# which suits the column layouts typical of discharge summaries better than
# the default PSM 3 for sparse clinical forms.
DEFAULT_OCR_CONFIG = "--oem 3 --psm 6"


class OCRService:
    """Thin, configurable wrapper around Tesseract via pytesseract."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._configured_cmd = (self._settings.tesseract_cmd or "").strip()
        if self._configured_cmd:
            # Never hardcoded — always the configured value.
            pytesseract.pytesseract.tesseract_cmd = self._configured_cmd
        self._language = self._settings.ocr_language or "eng"

    # ── Availability ─────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """True when a working Tesseract executable can be located."""
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:  # noqa: BLE001 - any failure means unavailable
            return False

    def ensure_available(self) -> None:
        """
        Raise OCRUnavailableError when Tesseract cannot be used.

        Called before any OCR attempt so callers get one predictable,
        actionable configuration error instead of a library traceback.
        """
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:  # noqa: BLE001
            hint = (
                "TESSERACT_CMD is set but the executable could not be run."
                if self._configured_cmd
                else "Set TESSERACT_CMD to the full path of the tesseract "
                "executable, or add Tesseract to the system PATH."
            )
            logger.error("Tesseract unavailable: %s", exc.__class__.__name__)
            raise OCRUnavailableError(
                "OCR is unavailable because Tesseract is not installed or not "
                f"configured on this server. {hint} The document was still "
                "saved; once Tesseract is installed, call the reprocess "
                "endpoint for this document instead of uploading it again.",
                internal_detail=f"tesseract check failed: {exc.__class__.__name__}",
            ) from exc

    # ── OCR ──────────────────────────────────────────────────────────────────

    def run_ocr(self, image: "Image.Image") -> str:
        """
        OCR a single PIL image and return raw text.

        Raises OCRUnavailableError if Tesseract is missing, and OCRFailedError
        if Tesseract runs but fails.
        """
        self.ensure_available()
        try:
            return pytesseract.image_to_string(
                image, lang=self._language, config=DEFAULT_OCR_CONFIG
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("OCR failed: %s", exc.__class__.__name__)
            raise OCRFailedError(
                "OCR failed to read text from the document.",
                internal_detail=f"tesseract error: {exc.__class__.__name__}",
            ) from exc

    def ocr_image_bytes(self, data: bytes) -> str:
        """Decode image bytes safely, then OCR them."""
        image = self.load_image(data)
        return self.run_ocr(image)

    @staticmethod
    def load_image(data: bytes) -> "Image.Image":
        """
        Open image bytes into a PIL image.

        Pillow is configured to refuse truncated files, and every decode
        failure is converted into a controlled CorruptedDocumentError.
        """
        try:
            image = Image.open(io.BytesIO(data))
            # Force a full decode now so corruption surfaces here rather
            # than midway through OCR.
            image.load()
            return image
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            logger.warning("Could not decode image: %s", exc.__class__.__name__)
            raise CorruptedDocumentError(
                "The image could not be read. It may be corrupted.",
                internal_detail=f"image decode failed: {exc.__class__.__name__}",
            ) from exc
