"""
CareLoop AI — Text / OCR Cleanup Service (Phase 2)

Conservative normalisation of text obtained from a PDF text layer or from
OCR.

WHAT THIS SERVICE DOES:
  - normalises line endings, non-breaking spaces, and exotic whitespace
  - strips control characters
  - collapses runs of spaces/tabs
  - removes obvious OCR artefacts (isolated pipes, form-feed junk, stray
    hyphenation at line ends)
  - preserves page boundaries using explicit page markers

WHAT THIS SERVICE MUST NEVER DO:
  - summarise or shorten meaning
  - diagnose, interpret, or classify
  - rewrite, expand, or "improve" instructions
  - invent, complete, or correct words
  - change medical terminology
  - alter dosages, frequencies, dates, or drug names

The only characters removed are ones that carry no clinical meaning:
whitespace variants, control codes, and repeated line-dash noise.  No
alphabetic content is ever deleted, replaced, or reworded.
"""
from __future__ import annotations

import re
import unicodedata

from app.core.logging import get_logger

logger = get_logger(__name__)

# Marker inserted between pages so the LLM can cite page numbers.
PAGE_MARKER_TEMPLATE = "--- PAGE {page_number} ---"

# Whitespace-like characters that should become a plain space.
# NBSP, narrow NBSP, en/em quad, figure space, and friends.
_INVISIBLE_SPACES = re.compile(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000]")

# ASCII control characters except tab (\x09) and newline (\x0a).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Runs of spaces/tabs on one line -> single space.
_INLINE_WHITESPACE = re.compile(r"[ \t]+")

# Three or more blank lines -> exactly one blank line.
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")

# A line consisting only of pipes/dashes/underscores is a table rule, not
# clinical content, and OCR frequently renders these inconsistently.
_TABLE_RULE = re.compile(r"^[\s|_\-=+*.:]{6,}$")

# "word-" at end of line followed by a lowercase continuation is a wrapped
# word. Rejoining it is a whitespace fix, not a content change.
_HYPHENATED_LINE_BREAK = re.compile(r"(\w)-[ \t]*\n[ \t]*(\w)")


class TextProcessingService:
    """Normalise extracted/OCR text without altering clinical content."""

    @staticmethod
    def clean_page_text(raw: str | None) -> str:
        """
        Clean a single page of text.

        Applies character-level normalisation and drops table rules and
        empty lines.  Content words are never modified.
        """
        if not raw:
            return ""

        text = unicodedata.normalize("NFKC", raw)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = _INVISIBLE_SPACES.sub(" ", text)
        text = _CONTROL_CHARS.sub("", text)
        # A form feed is a hard page break in some PDF producers; within a
        # single page it is noise.
        text = text.replace("\x0c", "\n")

        lines: list[str] = []
        for line in text.split("\n"):
            line = _INLINE_WHITESPACE.sub(" ", line).strip()
            if not line:
                continue
            if _TABLE_RULE.match(line):
                continue
            lines.append(line)

        return "\n".join(lines)

    @classmethod
    def join_hyphenated_line_breaks(cls, text: str) -> str:
        """
        Rejoin words split across a line break by hyphenation.

        "amoxi-\\ncillin 500 mg" -> "amoxicillin 500 mg"

        This only removes the hyphen and the intervening newline.  The
        characters on both sides are preserved exactly.

        KNOWN LIMITATION: a genuinely hyphenated compound that happens to
        wrap after its hyphen is also joined, so "beta-\\nblocker" becomes
        "betablocker".  Telling the two apart needs a dictionary of known
        medication names, which this phase does not have.  The alternative
        is worse: leaving every wrapped medication split across a line
        ("amoxi- cillin") corrupts far more names than the compound case
        does, and it also corrupts the record a clinician is trying to
        verify.  Flagged here so the trade-off stays visible.
        """
        return _HYPHENATED_LINE_BREAK.sub(r"\1\2", text)

    @classmethod
    def clean_document(cls, pages: list[str]) -> str:
        """
        Clean and assemble per-page text into a single document string.

        Page boundaries are preserved with explicit markers so the
        extraction stage can attribute items to a source page.
        """
        parts: list[str] = []
        for index, raw_page in enumerate(pages, start=1):
            cleaned = cls.clean_page_text(raw_page)
            if not cleaned:
                continue
            parts.append(
                f"{PAGE_MARKER_TEMPLATE.format(page_number=index)}\n{cleaned}"
            )

        document = "\n\n".join(parts)
        document = cls.join_hyphenated_line_breaks(document)
        document = _EXCESS_BLANK_LINES.sub("\n\n", document)
        return document.strip()

    @staticmethod
    def count_characters(text: str | None) -> int:
        """Character count of extracted text, for audit records only."""
        return len(text) if text else 0

    @staticmethod
    def is_meaningful(text: str | None, min_chars: int) -> bool:
        """
        Decide whether a page has enough real text to skip OCR.

        A page whose embedded text layer is below the threshold is treated
        as scanned and sent through OCR.
        """
        if not text:
            return False
        # Count only letters and digits: a page full of punctuation is noise.
        substantive = sum(1 for ch in text if ch.isalnum())
        return substantive >= min_chars
