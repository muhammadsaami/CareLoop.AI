"""
CareLoop AI — Document Processing Service (Phase 2)

Turns an uploaded PDF/PNG/JPG into cleaned plain text.

PDF strategy:
  1. Try the embedded text layer per page (fast, exact).
  2. If a page's embedded text is missing or too thin, rasterise it at
     PDF_OCR_RENDER_DPI and run Tesseract on the image.
  3. Preserve page boundaries via explicit page markers.

Image strategy:
  1. Decode with Pillow (truncation-resistant).
  2. Run Tesseract.
  3. Fail with a controlled error for corrupt images or missing Tesseract.

This module reads documents.  It does not interpret their clinical content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import fitz  # PyMuPDF

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CareLoopError,
    CorruptedDocumentError,
    EmptyDocumentTextError,
    PageLimitExceededError,
)
from app.core.logging import get_logger
from app.services.ocr import OCRService
from app.services.text_processing import TextProcessingService

logger = get_logger(__name__)


@dataclass
class ProcessedDocument:
    """Cleaned text plus audit metadata from the processing stage."""

    text: str
    page_count: int
    ocr_used: bool
    ocr_page_count: int = 0
    char_count: int = 0
    per_page_char_counts: list[int] = field(default_factory=list)


class DocumentProcessingService:
    """Extracts text from PDFs and images, using OCR when necessary."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        ocr_service: Optional[OCRService] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._ocr = ocr_service or OCRService(self._settings)
        self._text = TextProcessingService()

    # ── Entry point ──────────────────────────────────────────────────────────

    def process(
        self, *, data: bytes, content_type: str, filename: str
    ) -> ProcessedDocument:
        """Dispatch to the PDF or image pipeline based on content type."""
        if content_type == "application/pdf":
            return self.process_pdf(data)
        return self.process_image(data)

    # ── PDF ──────────────────────────────────────────────────────────────────

    def process_pdf(self, data: bytes) -> ProcessedDocument:
        """
        Extract text from a PDF, OCR-ing any page that needs it.

        Raises CorruptedDocumentError for unreadable PDFs and
        PageLimitExceededError when the page budget is exceeded.
        """
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001 - PyMuPDF raises broadly
            logger.warning("Could not open PDF: %s", exc.__class__.__name__)
            raise CorruptedDocumentError(
                "The PDF could not be read. It may be corrupted.",
                internal_detail=f"pymupdf open failed: {exc.__class__.__name__}",
            ) from exc

        try:
            if document.is_encrypted and not document.authenticate(""):
                raise CorruptedDocumentError(
                    "The PDF is password protected and cannot be read.",
                    internal_detail="encrypted pdf",
                )

            page_count = document.page_count
            self._enforce_page_limit(page_count)

            min_chars = self._settings.ocr_min_chars_per_page
            dpi = self._settings.pdf_ocr_render_dpi

            pages: list[str] = []
            ocr_pages = 0

            for index in range(page_count):
                page = document.load_page(index)
                try:
                    embedded = page.get_text("text") or ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Text layer read failed on page %s: %s",
                        index + 1,
                        exc.__class__.__name__,
                    )
                    embedded = ""

                if self._text.is_meaningful(embedded, min_chars):
                    pages.append(embedded)
                    continue

                # Thin or empty text layer: treat the page as scanned.
                pages.append(self._ocr_pdf_page(page, dpi, index + 1))
                ocr_pages += 1

            return self._finalise(
                pages,
                page_count=page_count,
                ocr_used=ocr_pages > 0,
                ocr_page_count=ocr_pages,
            )

        finally:
            document.close()

    def _ocr_pdf_page(self, page, dpi: int, page_number: int) -> str:
        """Rasterise a single PDF page and OCR the resulting image."""
        try:
            # Alpha=False yields a plain RGB pixmap suitable for Tesseract.
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            image_bytes = pixmap.tobytes("png")
            text = self._ocr.ocr_image_bytes(image_bytes)
            logger.info(
                "OCR completed for scanned page: page=%s chars=%s",
                page_number,
                self._text.count_characters(text),
            )
            return text
        except CareLoopError:
            # Preserve the original domain error and its status code.  A
            # missing Tesseract must stay a 503 (server misconfiguration),
            # not be misreported to the client as a 422 corrupt document.
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OCR of PDF page failed: page=%s error=%s",
                page_number,
                exc.__class__.__name__,
            )
            raise CorruptedDocumentError(
                f"Page {page_number} could not be read for OCR.",
                internal_detail=f"page ocr failed: {exc.__class__.__name__}",
            ) from exc

    # ── Image ────────────────────────────────────────────────────────────────

    def process_image(self, data: bytes) -> ProcessedDocument:
        """
        OCR a standalone PNG/JPG/JPEG upload.

        Raises CorruptedDocumentError for unreadable images and
        OCRUnavailableError when Tesseract is not configured.
        """
        text = self._ocr.ocr_image_bytes(data)
        if not self._text.is_meaningful(
            text, self._settings.ocr_min_chars_per_page
        ):
            logger.info("Image OCR produced no substantive text")
        return self._finalise([text], page_count=1, ocr_used=True, ocr_page_count=1)

    # ── Shared ───────────────────────────────────────────────────────────────

    def _enforce_page_limit(self, page_count: int) -> None:
        limit = self._settings.max_document_pages
        if page_count > limit:
            logger.warning(
                "Page limit exceeded: pages=%s limit=%s", page_count, limit
            )
            raise PageLimitExceededError(
                f"The document has {page_count} pages, which exceeds the "
                f"maximum of {limit}."
            )

    def _finalise(
        self,
        pages: list[str],
        *,
        page_count: int,
        ocr_used: bool,
        ocr_page_count: int,
    ) -> ProcessedDocument:
        """Clean, assemble, and validate the extracted text."""
        document_text = self._text.clean_document(pages)

        if not self._text.is_meaningful(
            document_text, self._settings.ocr_min_chars_per_page
        ):
            raise EmptyDocumentTextError(
                "No readable text could be obtained from the document. "
                "If this is a scanned document, verify that Tesseract is "
                "installed and OCR_LANGUAGE matches the document language."
            )

        return ProcessedDocument(
            text=document_text,
            page_count=page_count,
            ocr_used=ocr_used,
            ocr_page_count=ocr_page_count,
            char_count=self._text.count_characters(document_text),
            per_page_char_counts=[
                self._text.count_characters(page) for page in pages
            ],
        )
