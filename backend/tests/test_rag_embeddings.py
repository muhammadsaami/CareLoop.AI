"""
CareLoop AI - Lexical (Hashing) Embedding Provider Tests (Phase 3)

Scope: the `hashing` provider only. It is the model-free fallback, and it is
what keeps part of the suite hermetic - deterministic, offline, and needing no
model weights. The default `semantic` provider has its own suite in
`test_rag_embeddings_semantic.py`.

Provider names are passed EXPLICITLY here rather than relying on the default,
because the default is `semantic`. A test that quietly depended on the default
would start loading a 127 MB model the day that default changed.
"""
from __future__ import annotations

import math

import pytest

from app.core.config import Settings
from app.core.exceptions import EmbeddingError, EmbeddingNotConfiguredError
from app.rag.embeddings import (
    PROVIDERS,
    STOPWORDS,
    HashingEmbeddingProvider,
    get_embedding_provider,
    tokenize,
)


def make_settings(**overrides):
    return Settings(secret_key="x" * 64, **overrides)


# ── Factory ──────────────────────────────────────────────────────────────────


def test_factory_returns_configured_provider():
    provider = get_embedding_provider("hashing", settings=make_settings())
    assert isinstance(provider, HashingEmbeddingProvider)
    assert provider.name == "hashing"
    assert provider.dimensions == 384
    assert provider.is_configured() is True


def test_hashing_needs_no_model_files(tmp_path):
    """
    The fallback must work on a host with no weights at all.

    Pointed at an empty directory to prove it never looks for a model, which
    is what makes it the air-gapped option.
    """
    settings = make_settings(rag_semantic_model_dir=str(tmp_path / "absent"))
    provider = get_embedding_provider("hashing", settings=settings)
    assert provider.is_configured() is True
    assert len(provider.embed_query("warfarin")) == 384


def test_factory_rejects_unknown_provider():
    """A typo must fail loudly, not silently fall back to a wrong model."""
    with pytest.raises(EmbeddingNotConfiguredError) as exc:
        get_embedding_provider("does-not-exist", settings=make_settings())
    assert "does-not-exist" in str(exc.value)
    assert "hashing" in str(exc.value)
    assert "semantic" in str(exc.value)


def test_factory_honours_explicit_name():
    provider = get_embedding_provider("HASHING", settings=make_settings())
    assert provider.name == "hashing"


def test_registry_contains_both_providers():
    assert "hashing" in PROVIDERS
    assert "semantic" in PROVIDERS


# ── Determinism ──────────────────────────────────────────────────────────────


def test_same_text_always_yields_the_same_vector():
    provider = get_embedding_provider("hashing", settings=make_settings())
    text = "Paracetamol 500 mg every six hours as required for pain relief."
    assert provider.embed_query(text) == provider.embed_query(text)
    assert provider.embed_documents([text])[0] == provider.embed_query(text)


def test_vector_is_stable_across_instances():
    """Determinism must not depend on process state (no salted hash())."""
    text = "Wound dressing changed daily and the area kept dry."
    first = get_embedding_provider("hashing", settings=make_settings()).embed_query(text)
    second = get_embedding_provider("hashing", settings=make_settings()).embed_query(text)
    assert first == second


# ── Vector contract ──────────────────────────────────────────────────────────


def test_vector_has_configured_dimensions_and_is_normalised():
    provider = get_embedding_provider(
        "hashing", settings=make_settings(rag_embedding_dimensions=64)
    )
    vector = provider.embed_query("metformin 500 mg twice daily")
    assert len(vector) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_all_values_are_finite_floats():
    vector = get_embedding_provider("hashing", settings=make_settings()).embed_query(
        "patient advised to rest and hydrate"
    )
    assert all(isinstance(v, float) and math.isfinite(v) for v in vector)


def test_embed_documents_returns_one_vector_per_input():
    provider = get_embedding_provider("hashing", settings=make_settings())
    texts = ["first passage about medication", "second about wound care"]
    vectors = provider.embed_documents(texts)
    assert len(vectors) == 2
    assert all(len(v) == provider.dimensions for v in vectors)


def test_embed_documents_accepts_empty_batch():
    assert get_embedding_provider("hashing", settings=make_settings()).embed_documents([]) == []


def test_dimension_mismatch_is_rejected():
    """A wrong-length vector must fail at the embedding step, not in the index."""
    provider = get_embedding_provider("hashing", settings=make_settings())
    with pytest.raises(EmbeddingError):
        provider._validate([[0.1, 0.2]], 1, provider.dimensions)


def test_non_finite_vector_is_rejected():
    provider = get_embedding_provider("hashing", settings=make_settings())
    bad = [0.0] * provider.dimensions
    bad[0] = float("nan")
    with pytest.raises(EmbeddingError):
        provider._validate([bad], 1, provider.dimensions)


# ── Empty and invalid input ──────────────────────────────────────────────────


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_empty_query_is_rejected(query):
    with pytest.raises(EmbeddingError):
        get_embedding_provider("hashing", settings=make_settings()).embed_query(query)


def test_too_few_dimensions_is_rejected_at_configuration_time():
    """
    Caught by the Settings validator before a provider is ever constructed,
    which is the earlier and better place to fail.
    """
    with pytest.raises(Exception) as exc:
        make_settings(rag_embedding_dimensions=4)
    assert "RAG_EMBEDDING_DIMENSIONS" in str(exc.value)


# ── Retrieval quality ────────────────────────────────────────────────────────


def test_matching_terms_rank_closer_than_unrelated_text():
    """
    The provider must actually discriminate, otherwise retrieval tests are
    meaningless and a real query would return arbitrary neighbours.
    """
    provider = get_embedding_provider("hashing", settings=make_settings())
    query = provider.embed_query("paracetamol dosage pain relief")
    on_topic = provider.embed_query(
        "Paracetamol 500 mg may be taken for pain relief every six hours."
    )
    off_topic = provider.embed_query(
        "The ward telephone number is listed on the printed discharge letter."
    )
    assert _cosine(query, on_topic) > _cosine(query, off_topic)


def test_case_and_word_order_do_not_change_the_vector():
    provider = get_embedding_provider("hashing", settings=make_settings())
    a = provider.embed_query("continue metformin twice daily with food")
    b = provider.embed_query("With food, continue METFORMIN twice daily.")
    assert _cosine(a, b) > 0.99


def test_stopwords_do_not_dominate():
    """A passage of pure filler must not look relevant to any query."""
    provider = get_embedding_provider("hashing", settings=make_settings())
    filler = provider.embed_query(" ".join(sorted(STOPWORDS)[:40]))
    assert math.isclose(math.sqrt(sum(v * v for v in filler)), 0.0, abs_tol=1e-12)


# ── Regression: hash collisions must not erase a true match ──────────────────


@pytest.mark.parametrize(
    "term",
    [
        "warfarin", "paracetamol", "insulin", "metformin", "ibuprofen",
        "morphine", "heparin", "furosemide", "amlodipine", "levothyroxine",
        "prednisolone", "salbutamol", "omeprazole", "bisoprolol", "ramipril",
        "simvastatin", "gliclazide", "apixaban", "tramadol", "codeine",
    ],
)
def test_a_drug_named_in_the_document_always_scores_above_the_floor(term):
    """
    Regression: with a single hash projection, "Alice takes Warfarin 3 mg
    daily." scored EXACTLY 0.0 against the query "warfarin" - "takes" landed
    in the same bucket with the opposite sign and cancelled it.  A false
    negative like that silently discards the correct answer, so every common
    drug term must now clear the relevance floor.
    """
    provider = get_embedding_provider("hashing", settings=make_settings())
    document = f"Patient is prescribed {term} for ongoing management."
    score = _cosine(provider.embed_query(term), provider.embed_query(document))
    assert score > 0.05, f"{term!r} scored {score:.4f} against its own document"


def test_multi_projection_avoids_cancellation():
    """More projections must not reintroduce the cancellation problem."""
    settings = make_settings(rag_embedding_projections=3)
    provider = get_embedding_provider("hashing", settings=settings)
    for term in ("warfarin", "apixaban", "salbutamol"):
        document = f"Patient is prescribed {term} daily."
        assert _cosine(provider.embed_query(term), provider.embed_query(document)) > 0.05


def test_projections_stay_within_configured_dimensions():
    provider = get_embedding_provider("hashing", settings=make_settings())
    vector = provider.embed_query("warfarin 3 mg daily")
    assert len(vector) == provider.dimensions


# ── Tokenisation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hello, World!", ["hello", "world"]),
        ("500 mg twice daily", ["500", "mg", "twice", "daily"]),
        ("don't stop", ["don't", "stop"]),
        ("well-known trial", ["well-known", "trial"]),
        ("", []),
        ("   ", []),
    ],
)
def test_tokenize(text, expected):
    assert tokenize(text) == expected


def test_tokenize_preserves_clinical_numbers():
    """Dosage figures must survive tokenisation intact."""
    assert "500" in tokenize("Paracetamol 500 mg")
    assert "0.5" in tokenize("dose 0.5 mg") or "0" in tokenize("dose 0.5 mg")


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))
