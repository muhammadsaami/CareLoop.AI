"""
CareLoop AI - RAG Document Chunking (Phase 3)

Splits a Phase 2 `extracted_text` value into retrievable chunks.

INPUT FORMAT
`DischargeDocument.extracted_text` already contains explicit page markers of
the form `--- PAGE 3 ---`, written by `services.text_processing`.  This
module parses that existing structure rather than re-deriving pages, so no
document text is ever invented here.

DESIGN DECISIONS
  * Chunks NEVER span a page boundary.  This is the single most important
    property for traceability: every chunk's `source_page` is then exactly
    the page it came from, so a retrieved citation can never be a guess or
    an average.  Overlap is likewise confined to a single page.
  * `source_text` is stored VERBATIM.  The only transformation is choosing
    where to cut; no word is rewritten, reordered, summarised, or removed.
  * Splitting prefers a paragraph break, then a sentence end, and only cuts
    mid-sentence as a last resort.  That keeps medication lines and dosages
    intact far more often than a fixed-width window would.
  * Chunk IDs are deterministic (`{document_id}:{page}:{index}`), so
    re-indexing the same document produces the same IDs and therefore
    upserts instead of duplicating.

HEALTHCARE SAFETY BOUNDARY
This module is pure text segmentation.  It does not interpret, classify,
summarise, diagnose, or rewrite anything.  Text that is too short to be
useful is DROPPED, never padded or invented.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Matches the marker exactly as produced by services.text_processing.
# The non-greedy body lets us split a whole document on markers in one pass.
_PAGE_MARKER_RE = re.compile(
    r"^---\s*PAGE\s+(?P<page>\d+)\s*---\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Sentence boundary: terminator followed by whitespace.  Deliberately
# conservative - it requires punctuation, so abbreviations and decimals such
# as "500 mg." or "Dr." do not create false splits.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

# Document text that is too short to be useful is dropped, not padded.
_DROP_BELOW = 1  # characters; effective floor is settings.rag_min_chunk_chars


@dataclass(frozen=True)
class TextChunk:
    """
    One retrievable unit of a discharge document.

    `text` is the verbatim source slice.  `page` is the page it came from
    and is never inferred from position in the document.
    """

    chunk_id: str
    document_id: uuid.UUID
    patient_id: uuid.UUID
    text: str
    page: Optional[int]
    index: int
    text_sha256: str = ""
    extraction_run_id: Optional[uuid.UUID] = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)


class DocumentChunker:
    """Deterministic, page-aware chunker for extracted document text."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._chunk_size = int(self._settings.rag_chunk_size)
        self._overlap = int(self._settings.rag_chunk_overlap)
        self._min_chars = int(self._settings.rag_min_chunk_chars)

    # ── Public API ──────────────────────────────────────────────────────────

    def chunk_document(
        self,
        *,
        document_id: uuid.UUID,
        patient_id: uuid.UUID,
        text: str,
        extraction_run_id: Optional[uuid.UUID] = None,
    ) -> list[TextChunk]:
        """
        Split `text` into page-scoped chunks.

        Returns an empty list when the text yields no usable chunk; the
        caller decides whether that is an error.  Never raises on empty or
        marker-less input beyond returning `[]`.
        """
        if not text or not text.strip():
            return []

        pages = self.split_pages(text)
        chunks: list[TextChunk] = []
        index = 0

        for page_number, page_text in pages:
            for piece in self._split_page(page_text):
                if len(piece.strip()) < max(self._min_chars, _DROP_BELOW):
                    # Too short to carry meaning. Dropped, never padded.
                    continue
                chunks.append(
                    self._build_chunk(
                        document_id=document_id,
                        patient_id=patient_id,
                        page_number=page_number,
                        piece=piece,
                        index=index,
                        extraction_run_id=extraction_run_id,
                    )
                )
                index += 1

        logger.info(
            "Document chunked: document=%s pages=%s chunks=%s chunk_size=%s "
            "overlap=%s",
            document_id,
            len(pages),
            len(chunks),
            self._chunk_size,
            self._overlap,
        )
        return chunks

    def split_pages(self, text: str) -> list[tuple[Optional[int], str]]:
        """
        Split document text into `(page_number, page_text)` pairs.

        Text appearing BEFORE the first page marker is kept with page
        `None` rather than being attributed to page 1.  Guessing a page
        number would be exactly the fabricated citation this module exists
        to prevent.
        """
        if not text:
            return []

        matches = list(_PAGE_MARKER_RE.finditer(text))
        if not matches:
            # No markers at all: one un-attributed segment.  We do not
            # pretend to know the page.
            return [(None, text.strip())] if text.strip() else []

        pages: list[tuple[Optional[int], str]] = []

        preamble = text[: matches[0].start()].strip()
        if preamble:
            pages.append((None, preamble))

        for position, match in enumerate(matches):
            start = match.end()
            end = (
                matches[position + 1].start()
                if position + 1 < len(matches)
                else len(text)
            )
            body = text[start:end].strip()
            page_number = int(match.group("page"))
            if body:
                pages.append((page_number, body))

        return pages

    @staticmethod
    def build_chunk_id(
        document_id: uuid.UUID, page: Optional[int], index: int
    ) -> str:
        """
        Deterministic chunk identifier.

        Stable across re-indexing of the same document, which is what makes
        indexing idempotent: the same chunk always overwrites itself.
        """
        page_part = "na" if page is None else str(page)
        return f"{document_id}:{page_part}:{index:04d}"

    # ── Internals ───────────────────────────────────────────────────────────

    def _split_page(self, page_text: str) -> list[str]:
        """
        Split one page's text into overlapping pieces.

        Deterministic: the same input always yields the same pieces, with
        boundaries chosen on paragraph, then sentence, then word edges.
        """
        if len(page_text) <= self._chunk_size:
            return [page_text]

        pieces: list[str] = []
        cursor = 0
        length = len(page_text)
        # A snapped boundary must still leave a piece worth indexing.  Without
        # this floor a boundary landing close to `cursor` collapses the window
        # and the loop degenerates into one-character strides.
        min_piece = max(self._overlap + 1, self._chunk_size // 2)

        while cursor < length:
            hard_end = min(cursor + self._chunk_size, length)
            end = hard_end
            if hard_end < length:
                boundary = self._find_boundary(page_text, cursor, hard_end, min_piece)
                if boundary > cursor:
                    end = boundary
            piece = page_text[cursor:end].strip()
            if piece:
                pieces.append(piece)
            if end >= length:
                break
            # Because every accepted boundary is at least `min_piece` (and
            # therefore at least `overlap + 1`) characters past `cursor`, the
            # next cursor always advances.  The config validator guarantees
            # `overlap < chunk_size`, which covers the un-snapped case.
            cursor = end - self._overlap

        return pieces

    @staticmethod
    def _find_boundary(
        text: str, start: int, hard_end: int, min_piece: int
    ) -> int:
        """
        Choose the best cut point at or before `hard_end`.

        Preference order: paragraph break, then sentence end, then a space.
        Every candidate must sit at least `min_piece` characters past `start`
        so the resulting chunk stays useful.  Returns `hard_end` when the
        window has no usable break, which is the correct behaviour for e.g. a
        long unbroken string.
        """
        window = text[start:hard_end]
        floor = start + min_piece

        paragraph = window.rfind("\n\n")
        if paragraph > 0 and start + paragraph + 2 >= floor:
            return start + paragraph + 2

        best = -1
        for match in _SENTENCE_END_RE.finditer(window):
            candidate = start + match.end()
            if candidate < floor:
                continue
            if candidate > best:
                best = candidate

        if best < 0:
            last_space = window.rfind(" ")
            if last_space > 0 and start + last_space + 1 >= floor:
                best = start + last_space + 1

        if best < 0:
            return hard_end
        return min(best, hard_end)

    def _build_chunk(
        self,
        *,
        document_id: uuid.UUID,
        patient_id: uuid.UUID,
        page_number: Optional[int],
        piece: str,
        index: int,
        extraction_run_id: Optional[uuid.UUID],
    ) -> TextChunk:
        chunk_id = self.build_chunk_id(document_id, page_number, index)
        return TextChunk(
            chunk_id=chunk_id,
            document_id=document_id,
            patient_id=patient_id,
            text=piece,
            page=page_number,
            index=index,
            text_sha256=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
            extraction_run_id=extraction_run_id,
        )
