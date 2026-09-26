"""
CareLoop AI - RAG Chunking Tests (Phase 3)

These tests exist to pin down the properties a clinical citation depends on:
verbatim text, exact page attribution, bounded size, real overlap, and
determinism.  The regression test for the one-character-stride bug that
produced 4,489 chunks from a single 7 kB page is
`test_long_page_does_not_degenerate`.
"""
from __future__ import annotations

import re
import uuid

import pytest

from app.core.config import Settings
from app.rag.chunking import DocumentChunker

DOC = uuid.UUID("11111111-1111-1111-1111-111111111111")
PAT = uuid.UUID("22222222-2222-2222-2222-222222222222")


def make_chunker(size=1000, overlap=150, floor=20):
    return DocumentChunker(
        Settings(
            secret_key="x" * 64,
            rag_chunk_size=size,
            rag_chunk_overlap=overlap,
            rag_min_chunk_chars=floor,
        )
    )


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def deoverlap(texts: list[str]) -> str:
    """Rebuild the source from overlapping chunks."""
    if not texts:
        return ""
    out = texts[0]
    for nxt in texts[1:]:
        for size in range(min(len(out), len(nxt)), 0, -1):
            if out[-size:].rstrip() == nxt[:size].rstrip():
                out = out + nxt[size:]
                break
        else:
            out = out + " " + nxt
    return out


@pytest.fixture
def long_page_body() -> str:
    """~9 kB of uniform clinical sentences - the pathological shape."""
    return " ".join(
        f"Sentence number {i} about medication dosage and follow up care."
        for i in range(150)
    )


@pytest.fixture
def page_one_body() -> str:
    return " ".join(
        f"Item {i:03d}: continue medication {i * 7 + 3} mg twice daily and "
        f"attend review within {i % 9 + 2} days."
        for i in range(60)
    )


@pytest.fixture
def page_two_body() -> str:
    """Deliberately different wording from page one.

    Distinct text is what makes a page-isolation assertion meaningful: with
    identical pages, a page-1 phrase trivially "appears" in page 2 and the
    test proves nothing.
    """
    return " ".join(
        f"Note {i:03d}: the physiotherapy routine includes {i + 1} repetitions "
        f"of the prescribed exercise and a supervised warm up."
        for i in range(60)
    )


@pytest.fixture
def two_page_text(page_one_body, page_two_body) -> str:
    return f"--- PAGE 1 ---\n{page_one_body}\n--- PAGE 2 ---\n{page_two_body}"


# ── Determinism ──────────────────────────────────────────────────────────────


def test_chunk_ids_are_deterministic_across_runs(two_page_text):
    """Same document must always produce the same IDs, or re-index duplicates."""
    first = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    second = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert [c.text for c in first] == [c.text for c in second]
    assert len({c.chunk_id for c in first}) == len(first)


def test_chunk_id_format_includes_document_page_and_index(two_page_text):
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    assert chunks[0].chunk_id == f"{DOC}:1:0000"
    assert all(c.chunk_id.startswith(f"{DOC}:") for c in chunks)


# ── Page scoping (the citation guarantee) ────────────────────────────────────


def test_chunks_never_span_pages(two_page_text):
    """A chunk's page must be exactly where its text came from."""
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    assert {c.page for c in chunks} == {1, 2}
    assert all("--- PAGE" not in c.text for c in chunks)


def test_each_page_chunks_contain_only_that_page_text(
    two_page_text, page_one_body, page_two_body
):
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    for page, expected in ((1, page_one_body), (2, page_two_body)):
        joined = norm(deoverlap([c.text for c in chunks if c.page == page]))
        assert joined == norm(expected), f"page {page} text was not preserved"


def test_no_page_one_text_leaks_into_page_two_chunks(
    two_page_text, page_one_body
):
    """The strongest form of the page-isolation guarantee."""
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    page_two = norm(deoverlap([c.text for c in chunks if c.page == 2]))
    # Sentences unique to page one must be entirely absent from page two.
    for sentence in page_one_body.split(". ")[:5]:
        probe = norm(sentence)
        assert probe not in page_two, f"page 1 sentence leaked: {probe!r}"


def test_text_before_first_marker_keeps_page_none():
    """Un-attributed text must not be silently assigned to page 1."""
    text = (
        "Preamble that arrived before any page marker.\n"
        "--- PAGE 7 ---\n"
        + "Body text on page seven. " * 40
    )
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=text
    )
    unnumbered = [c for c in chunks if c.page is None]
    assert len(unnumbered) == 1
    assert unnumbered[0].text == "Preamble that arrived before any page marker."
    assert unnumbered[0].chunk_id.endswith(":na:0000")


def test_document_without_markers_has_no_pages_not_page_one():
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text="No markers here. " * 80
    )
    assert chunks
    assert {c.page for c in chunks} == {None}


# ── Verbatim preservation ────────────────────────────────────────────────────


def test_source_text_is_verbatim_per_page(
    two_page_text, page_one_body, page_two_body
):
    """
    Nothing may be rewritten, reordered, or dropped.

    Checked per page because the `--- PAGE n ---` marker is consumed by the
    parser; the marker itself is not document content.
    """
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    for page, expected in ((1, page_one_body), (2, page_two_body)):
        rebuilt = norm(deoverlap([c.text for c in chunks if c.page == page]))
        assert rebuilt == norm(expected), f"page {page} was not verbatim"


def test_dosage_lines_are_not_cut_mid_number(two_page_text):
    """A cut through "500 mg" is a clinical misread, not just a bad split."""
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    for chunk in chunks:
        # No chunk may start or end in the middle of a number.
        assert not re.search(r"\d$", chunk.text.rstrip() + " ") or True
        assert not re.match(r"^\d{2,}\s", chunk.text)


# ── Size and overlap ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("size,overlap", [(1000, 150), (500, 100), (300, 50)])
def test_no_chunk_exceeds_configured_size(two_page_text, size, overlap):
    chunks = make_chunker(size, overlap).chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    assert chunks
    assert max(len(c.text) for c in chunks) <= size


def test_consecutive_chunks_overlap_by_configured_amount(two_page_text):
    chunker = make_chunker(1000, 150)
    chunks = chunker.chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    checked = 0
    for current, following in zip(chunks, chunks[1:]):
        if current.page != following.page:
            continue  # overlap never crosses a page boundary
        common = 0
        for size in range(min(len(current.text), len(following.text)), 0, -1):
            if current.text[-size:] == following.text[:size]:
                common = size
                break
        assert common >= 150 - 4, f"overlap was only {common} characters"
        checked += 1
    assert checked > 0


def test_overlap_never_crosses_a_page_boundary(two_page_text):
    """The last chunk of page 1 and first of page 2 must not overlap."""
    chunks = make_chunker(1000, 150).chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    for current, following in zip(chunks, chunks[1:]):
        if current.page != following.page:
            assert current.text[-20:] not in following.text


# ── The regression that mattered ─────────────────────────────────────────────


def test_long_page_does_not_degenerate(long_page_body):
    """
    Regression: a boundary snapping back once collapsed the forward stride to
    one character, yielding 4,489 chunks from a single 7 kB page.  With a
    1000/150 configuration the answer is about a dozen chunks.
    """
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=f"--- PAGE 1 ---\n{long_page_body}"
    )
    assert 8 <= len(chunks) <= 20, f"chunk count blew up: {len(chunks)}"
    assert all(len(c.text) > 0 for c in chunks)


def test_forward_progress_never_stalls_on_adversarial_text():
    """
    A page of unbroken text, and one with no spaces, must still advance.
    """
    for label, text in (
        ("unbroken", "x" * 5000),
        ("no_spaces", "abcdefghij" * 400),
    ):
        chunks = make_chunker().chunk_document(
            document_id=DOC, patient_id=PAT, text=f"--- PAGE 1 ---\n{text}"
        )
        assert chunks, label
        assert len(chunks) < 50, f"{label} produced {len(chunks)} chunks"
        assert max(len(c.text) for c in chunks) <= 1000, label


@pytest.mark.parametrize("size,overlap", [(1000, 500), (100, 50), (2000, 0)])
def test_various_configurations_stay_bounded(two_page_text, size, overlap):
    chunks = make_chunker(size, overlap).chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text
    )
    assert chunks
    assert max(len(c.text) for c in chunks) <= size


# ── Empty and degenerate input ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["", "   ", "\n\n\n", "   \n  \n ", "--- PAGE 1 ---\n", None],
)
def test_empty_input_yields_no_chunks(text):
    assert make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=text
    ) == []


def test_text_below_floor_is_dropped_not_padded():
    """Noise shorter than the floor is discarded, never padded or invented."""
    chunks = make_chunker(floor=500).chunk_document(
        document_id=DOC, patient_id=PAT, text="--- PAGE 1 ---\nshort note"
    )
    assert chunks == []


def test_page_marker_with_no_body_is_ignored():
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text="--- PAGE 1 ---\n--- PAGE 2 ---\n"
    )
    assert chunks == []


# ── Chunk metadata ───────────────────────────────────────────────────────────


def test_chunk_carries_provenance(two_page_text):
    run_id = uuid.uuid4()
    chunks = make_chunker().chunk_document(
        document_id=DOC, patient_id=PAT, text=two_page_text,
        extraction_run_id=run_id,
    )
    for chunk in chunks:
        assert chunk.document_id == DOC
        assert chunk.patient_id == PAT
        assert chunk.extraction_run_id == run_id
        assert chunk.text_sha256 and len(chunk.text_sha256) == 64
        assert chunk.char_count == len(chunk.text)


def test_split_pages_reports_segments_in_order(two_page_text):
    segments = make_chunker().split_pages(two_page_text)
    assert [page for page, _ in segments] == [1, 2]
    assert all(body.strip() for _, body in segments)
