"""
CareLoop AI — Phase 2: Text Processing Tests

The normaliser must remove noise without ever altering clinical content:
a medication name or dosage must survive byte-for-byte.
"""
import pytest

from app.services.text_processing import PAGE_MARKER_TEMPLATE, TextProcessingService


@pytest.fixture
def tp():
    return TextProcessingService()


# ── Page cleaning ───────────────────────────────────────────────────────────

class TestCleanPageText:
    def test_none_and_empty_become_empty(self, tp):
        assert tp.clean_page_text(None) == ""
        assert tp.clean_page_text("") == ""

    def test_collapses_inline_whitespace(self, tp):
        assert tp.clean_page_text("Metformin    500   mg") == "Metformin 500 mg"

    def test_strips_leading_trailing_space(self, tp):
        assert tp.clean_page_text("   Metformin 500 mg   ") == "Metformin 500 mg"

    def test_normalises_windows_line_endings(self, tp):
        assert tp.clean_page_text("a\r\nb\rc") == "a\nb\nc"

    def test_removes_control_characters(self, tp):
        assert tp.clean_page_text("Metformin\x00 500\x07 mg") == "Metformin 500 mg"

    def test_converts_invisible_spaces(self, tp):
        """NBSP and friends become ordinary spaces."""
        raw = "Metformin\u00a0500\u2009mg"
        assert tp.clean_page_text(raw) == "Metformin 500 mg"

    def test_drops_empty_lines(self, tp):
        assert tp.clean_page_text("a\n\n\nb") == "a\nb"

    @pytest.mark.parametrize(
        "rule",
        [
            "--------------",
            "==========",
            "|  |  |  |  |",
            "____________",
            "::::::::::::::",
        ],
    )
    def test_drops_table_rules(self, tp, rule):
        assert tp.clean_page_text(f"Metformin\n{rule}\n500 mg") == (
            "Metformin\n500 mg"
        )

    def test_preserves_dosage_and_punctuation(self, tp):
        """Clinical content must not be mangled."""
        raw = "Take 1 tablet (500 mg) PO twice daily; avoid alcohol."
        assert tp.clean_page_text(raw) == raw

    def test_preserves_unicode_clinical_symbols(self, tp):
        raw = "Heart rate > 100 bpm, temperature 38.5 °C"
        assert "100 bpm" in tp.clean_page_text(raw)

    def test_tab_is_treated_as_whitespace(self, tp):
        assert tp.clean_page_text("Metformin\t500 mg") == "Metformin 500 mg"


# ── Hyphenated line breaks ──────────────────────────────────────────────────

class TestHyphenationRepair:
    def test_rejoins_split_word(self, tp):
        assert tp.join_hyphenated_line_breaks("amoxi-\ncillin 500 mg") == (
            "amoxicillin 500 mg"
        )

    def test_rejoins_with_indented_continuation(self, tp):
        assert tp.join_hyphenated_line_breaks("para-\n   cetamol 500 mg") == (
            "paracetamol 500 mg"
        )

    def test_rejoins_multiple_breaks(self, tp):
        raw = "amoxi-\ncillin and ibu-\nprofen"
        assert tp.join_hyphenated_line_breaks(raw) == "amoxicillin and ibuprofen"

    def test_compound_word_wrapped_after_hyphen_is_also_joined(self, tp):
        """
        Documented limitation: a real compound is indistinguishable from a
        hyphenation artifact without a dictionary. See the docstring on
        join_hyphenated_line_breaks.  Pinned here so the behaviour cannot
        change silently.
        """
        assert tp.join_hyphenated_line_breaks("beta-\nblocker") == "betablocker"

    def test_hyphen_within_a_single_line_is_untouched(self, tp):
        raw = "Take the beta-blocker as directed"
        assert tp.join_hyphenated_line_breaks(raw) == raw

    def test_real_medical_name_survives(self, tp):
        raw = "Patient was started on hydrochloro-\nthiazide 25 mg daily."
        assert "hydrochlorothiazide 25 mg" in tp.join_hyphenated_line_breaks(raw)


# ── Document assembly ───────────────────────────────────────────────────────

class TestCleanDocument:
    def test_pages_are_joined_with_markers(self, tp):
        doc = tp.clean_document(["First page content here.", "Second page content."])
        assert PAGE_MARKER_TEMPLATE.format(page_number=1) in doc
        assert PAGE_MARKER_TEMPLATE.format(page_number=2) in doc
        assert "First page content here." in doc
        assert "Second page content." in doc

    def test_empty_pages_are_skipped_but_keep_numbering(self, tp):
        """Page markers must reflect true page numbers, not kept pages."""
        doc = tp.clean_document(["Page one has real content.", "", "Page three here."])
        assert PAGE_MARKER_TEMPLATE.format(page_number=1) in doc
        assert PAGE_MARKER_TEMPLATE.format(page_number=2) not in doc
        assert PAGE_MARKER_TEMPLATE.format(page_number=3) in doc

    def test_all_empty_pages_yields_empty(self, tp):
        assert tp.clean_document(["", None, "   "]) == ""

    def test_result_is_stripped(self, tp):
        assert tp.clean_document(["  content  "]).endswith("content")

    def test_no_excessive_blank_runs(self, tp):
        doc = tp.clean_document(["a" * 50, "b" * 50, "c" * 50])
        assert "\n\n\n" not in doc


# ── Metrics ─────────────────────────────────────────────────────────────────

class TestMetrics:
    def test_count_characters(self, tp):
        assert tp.count_characters("abcd") == 4
        assert tp.count_characters("") == 0
        assert tp.count_characters(None) == 0

    def test_is_meaningful_requires_minimum(self, tp):
        assert tp.is_meaningful("abcdefghij", 10) is True
        assert tp.is_meaningful("abcdefghi", 10) is False

    def test_is_meaningful_ignores_punctuation(self, tp):
        """A page of dashes must not count as meaningful text."""
        assert tp.is_meaningful("----------" * 20, 40) is False

    def test_is_meaningful_counts_alphanumerics_only(self, tp):
        # 10 alnum chars plus lots of punctuation still clears a threshold
        # of 10 but not 40.
        assert tp.is_meaningful("a1b2c3d4e5" + "." * 100, 10) is True
        assert tp.is_meaningful("a1b2c3d4e5" + "." * 100, 40) is False

    def test_empty_and_none_are_not_meaningful(self, tp):
        assert tp.is_meaningful("", 1) is False
        assert tp.is_meaningful(None, 1) is False
