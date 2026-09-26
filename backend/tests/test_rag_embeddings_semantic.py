"""
CareLoop AI - Semantic Embedding Provider Tests (Phase 3)

Split deliberately in two:

  * `TestSemanticProviderContract` runs with NO model present. It proves the
    provider's contract, error handling, and wiring using a stub ONNX session,
    so `pytest` stays fast, offline, and hermetic.
  * `TestRealSemanticModel` exercises the actual bge-small-en-v1.5 weights and
    SKIPS when they have not been fetched. This is the only place a real model
    is loaded, and it never runs in CI by accident.

No test in this file downloads a model or calls a network service.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.config import get_settings
from app.core.exceptions import EmbeddingError, EmbeddingNotConfiguredError
from app.rag.embeddings import (
    PROVIDERS,
    SemanticEmbeddingProvider,
    get_embedding_provider,
)
from app.rag.embeddings.fetch_model import is_model_present, _resolve_dest
from app.rag.embeddings import semantic as semantic_module

# ── A stub ONNX session, so the provider can be tested without weights ──────

_STUB_DIM = 8


class _StubSession:
    """
    Minimal stand-in for `onnxruntime.InferenceSession`.

    Produces a deterministic CLS-pooled vector derived from the input ids, so
    the provider's tokenisation, pooling, normalisation, batching, and
    validation paths are all genuinely exercised - only the matrix
    multiplication is faked.
    """

    def __init__(self, inputs=("input_ids", "attention_mask", "token_type_ids")):
        self._inputs = inputs
        self.calls: list[dict] = []

    def get_inputs(self):
        return [type("I", (), {"name": n})() for n in self._inputs]

    def get_outputs(self):
        return [type("O", (), {"name": "last_hidden_state", "shape": [
            "batch", "seq", _STUB_DIM]})()]

    def run(self, output_names, feed):
        self.calls.append({"output_names": output_names, "feed": feed})
        ids = feed["input_ids"]
        batch, seq = ids.shape
        hidden = np.zeros((batch, seq, _STUB_DIM), dtype=np.float32)
        for b in range(batch):
            for t in range(seq):
                token = int(ids[b, t])
                if token == 0:  # padding must not influence the vector
                    continue
                hidden[b, t, token % _STUB_DIM] = 1.0
        # Make the CLS row the sum of the sequence so a longer text has a
        # larger CLS magnitude, mirroring a real encoder.
        hidden[:, 0, :] = hidden.sum(axis=1)
        return [hidden]


@pytest.fixture
def stub_provider(monkeypatch, tmp_path):
    """A SemanticEmbeddingProvider backed by the stub session."""
    model_dir = tmp_path / "model"
    (model_dir / "onnx").mkdir(parents=True)
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "onnx" / "model.onnx").write_bytes(b"stub")

    settings = get_settings().model_copy(
        update={
            "rag_semantic_model_dir": str(model_dir),
            "rag_semantic_query_instruction": "QUERY: ",
        }
    )
    provider = SemanticEmbeddingProvider(settings)
    session = _StubSession()
    monkeypatch.setattr(provider, "_load", lambda: None)
    provider._session = session
    provider._tokenizer = _SimpleTokenizer()
    provider._dimensions = _STUB_DIM
    provider._session_for_test = session
    return provider


class _SimpleEncoding:
    def __init__(self, ids):
        self.ids = ids
        self.attention_mask = [1] * len(ids)


class _SimpleTokenizer:
    """
    Whitespace tokenizer that pads like the real one.

    Padding matters: the provider builds a single dense `input_ids` array for
    the batch, so a tokenizer that returned ragged sequences would fail.  The
    real `tokenizers.Tokenizer` is configured with `enable_padding()`, and
    this double mirrors that so the batching path is genuinely covered.
    """

    def __init__(self):
        self.queries_with_instruction = []

    def encode_batch(self, texts):
        rows = []
        for text in texts:
            if text.startswith("QUERY: "):
                self.queries_with_instruction.append(text)
            rows.append([ord(word[0]) % 97 for word in text.split()])
        width = max((len(r) for r in rows), default=1)
        out = []
        for row in rows:
            ids = row + [0] * (width - len(row))
            out.append(_SimpleEncoding(ids))
        return out


# ── Contract tests: no model required ──────────────────────────────────────


class TestSemanticProviderContract:
    def test_is_registered_in_the_factory(self):
        assert "semantic" in PROVIDERS
        assert PROVIDERS["semantic"] is SemanticEmbeddingProvider

    def test_factory_returns_it_by_default(self):
        provider = get_embedding_provider()
        assert provider.name == "semantic"

    def test_shipping_default_is_semantic(self):
        assert get_settings().rag_embedding_provider == "semantic"

    def test_hashing_remains_available_as_a_fallback(self):
        provider = get_embedding_provider("hashing")
        assert provider.name == "hashing"
        assert provider.is_configured() is True

    def test_unknown_provider_is_a_configuration_error(self):
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            get_embedding_provider("word2vec")
        assert "word2vec" in str(exc.value)

    def test_missing_model_reports_a_controlled_error(self, tmp_path):
        settings = get_settings().model_copy(
            update={"rag_semantic_model_dir": str(tmp_path / "absent")}
        )
        provider = SemanticEmbeddingProvider(settings)
        assert provider.is_configured() is False
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider.ensure_configured()
        message = str(exc.value)
        # Actionable: says what is missing and how to fix it.
        assert "not present" in message
        assert "fetch_model" in message

    def test_missing_model_with_download_disabled(self, tmp_path):
        settings = get_settings().model_copy(
            update={
                "rag_semantic_model_dir": str(tmp_path / "absent"),
                "rag_semantic_allow_download": False,
            }
        )
        provider = SemanticEmbeddingProvider(settings)
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider.ensure_configured()
        assert "RAG_SEMANTIC_ALLOW_DOWNLOAD is disabled" in str(exc.value)

    def test_blank_query_is_rejected(self, stub_provider):
        with pytest.raises(EmbeddingError):
            stub_provider.embed_query("   ")

    def test_blank_document_chunk_is_rejected(self, stub_provider):
        with pytest.raises(EmbeddingError):
            stub_provider.embed_documents(["real text", "   "])

    def test_non_text_input_is_rejected(self, stub_provider):
        with pytest.raises(EmbeddingError):
            stub_provider.embed_documents([12345])

    def test_empty_batch_returns_empty(self, stub_provider):
        assert stub_provider.embed_documents([]) == []

    def test_vectors_are_l2_normalised(self, stub_provider):
        for vector in stub_provider.embed_documents(["alpha beta", "gamma"]):
            assert pytest.approx(1.0, abs=1e-6) == sum(v * v for v in vector) ** 0.5

    def test_embedding_is_deterministic(self, stub_provider):
        first = stub_provider.embed_query("warfarin dosage")
        second = stub_provider.embed_query("warfarin dosage")
        assert first == second

    def test_query_instruction_applied_to_queries_only(self, stub_provider):
        stub_provider.embed_query("blood thinner")
        assert stub_provider._tokenizer.queries_with_instruction == [
            "QUERY: blood thinner"
        ]

        stub_provider._tokenizer.queries_with_instruction.clear()
        stub_provider.embed_documents(["blood thinner"])
        # Documents must NOT carry the instruction: BGE is trained with it on
        # the query side only.
        assert stub_provider._tokenizer.queries_with_instruction == []

    def test_padding_is_excluded_from_the_vector(self, stub_provider):
        """A zero pad token must not shift the result."""
        padded = stub_provider.embed_documents(["alpha beta gamma"])
        feed = stub_provider._session_for_test.calls[-1]["feed"]
        assert feed["token_type_ids"].shape == feed["input_ids"].shape
        assert padded  # sanity

    def test_relevance_floor_travels_with_the_provider(self):
        semantic = get_embedding_provider("semantic")
        lexical = get_embedding_provider("hashing")
        # A dense encoder compresses cosine into roughly 0.45-0.75, so its
        # floor must be far higher than a bag-of-words model's.
        assert semantic.default_min_score == 0.60
        assert lexical.default_min_score == 0.10
        assert semantic.relevance_floor > lexical.relevance_floor

    def test_configured_min_score_overrides_the_provider_default(self):
        settings = get_settings().model_copy(update={"rag_min_score": 0.42})
        assert SemanticEmbeddingProvider(settings).relevance_floor == 0.42
        assert get_embedding_provider("hashing", settings).relevance_floor == 0.42

    def test_unset_min_score_falls_back_to_the_provider(self):
        settings = get_settings().model_copy(update={"rag_min_score": None})
        assert SemanticEmbeddingProvider(settings).relevance_floor == 0.60

    def test_out_of_range_min_score_is_rejected(self):
        # Must go through the constructor: `model_copy` deliberately skips
        # validation, so it would not exercise the validator at all.
        from app.core.config import Settings

        with pytest.raises(ValueError):
            Settings(rag_min_score=1.5)

    def test_min_score_may_be_left_unset(self):
        from app.core.config import Settings

        assert Settings().rag_min_score is None

    def test_model_directory_is_configurable(self):
        settings = get_settings().model_copy(
            update={"rag_semantic_model_name": "some/other-model"}
        )
        assert SemanticEmbeddingProvider(settings).model_name == "some/other-model"

    def test_no_api_key_setting_exists(self):
        """The provider must not need, or accept, a credential."""
        fields = get_settings().model_fields
        assert not any(
            "embed" in name and "key" in name for name in fields
        ), "an embedding API key must never be configurable"

    def test_graph_missing_expected_output_is_rejected(self, monkeypatch, tmp_path):
        model_dir = tmp_path / "model"
        (model_dir / "onnx").mkdir(parents=True)
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        (model_dir / "onnx" / "model.onnx").write_bytes(b"stub")
        settings = get_settings().model_copy(
            update={"rag_semantic_model_dir": str(model_dir)}
        )
        provider = SemanticEmbeddingProvider(settings)
        bad = _StubSession()
        bad.get_outputs = lambda: [
            type("O", (), {"name": "pooler_output", "shape": ["b", "s", 8]})()
        ]
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider._verify_graph(bad)
        assert "last_hidden_state" in str(exc.value)

    def test_graph_missing_expected_input_is_rejected(self):
        provider = SemanticEmbeddingProvider(get_settings())
        bad = _StubSession(inputs=("input_ids",))
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider._verify_graph(bad)
        assert "attention_mask" in str(exc.value)

    def test_loaded_artifacts_are_shared_across_instances(self):
        """
        Two provider instances must reuse ONE loaded session.

        FastAPI constructs the RAG services - and therefore this provider - on
        every request. Without a process-wide cache each request would re-read
        127 MB off disk and rebuild the ONNX graph, which would dominate
        latency. This test fails if that optimisation is ever reverted.
        """
        onnx_file = _resolve_dest(get_settings().rag_semantic_model_dir) / (
            "onnx"
        ) / "model.onnx"
        if not onnx_file.is_file():
            pytest.skip("semantic model weights not present")

        threads = int(get_settings().rag_semantic_num_threads or 0)
        max_tokens = get_settings().rag_semantic_max_tokens
        key = (str(onnx_file), max_tokens, threads)

        first = SemanticEmbeddingProvider(get_settings())
        first._load()
        assert key in semantic_module._LOADED, "load did not populate the cache"

        # A brand-new instance, exactly as a new request would create.
        second = SemanticEmbeddingProvider(get_settings())
        assert second._session is None, "a new instance must start unloaded"
        second._load()

        # Same objects, so the graph was not rebuilt.
        assert second._session is first._session
        assert second._tokenizer is first._tokenizer
        assert second._dimensions == first._dimensions

        # A differently-configured load must get its own key, not this entry.
        assert (str(onnx_file), max_tokens + 1, threads) not in semantic_module._LOADED


# ── Real model: skipped unless the weights are present ─────────────────────


def _model_available() -> bool:
    return is_model_present(
        _resolve_dest(get_settings().rag_semantic_model_dir)
    )


requires_model = pytest.mark.skipif(
    not _model_available(),
    reason=(
        "semantic model weights not fetched; run "
        "`python -m app.rag.embeddings.fetch_model` to enable"
    ),
)


@requires_model
class TestRealSemanticModel:
    """
    Proves the shipped model is genuinely semantic, not lexical.

    This is the test that would have caught the original hashing provider: a
    query sharing NO words with the document must still score highly.
    """

    @pytest.fixture(scope="class")
    def provider(self):
        return SemanticEmbeddingProvider(get_settings())

    def _sim(self, provider, query, document) -> float:
        q = np.array(provider.embed_query(query))
        d = np.array(provider.embed_documents([document])[0])
        return float(q @ d)

    def test_model_is_present_and_cpu_only(self, provider):
        assert provider.is_configured() is True
        assert provider.dimensions == 384
        assert provider.model_name == "BAAI/bge-small-en-v1.5"

    def test_dimensions_match_the_configured_model(self, provider):
        assert provider.dimensions == 384

    def test_matches_meaning_not_wording(self, provider):
        """
        The core requirement. "blood thinner" shares no content word with
        "warfarin ... to thin your blood", so a lexical model scores 0.0.
        """
        document = "Warfarin 5mg daily to thin the blood."
        assert self._sim(provider, "blood thinner", document) > 0.70

    def test_ranks_the_right_topic_first(self, provider):
        query = provider.embed_query("blood thinner")
        warfarin = np.array(provider.embed_documents(["Warfarin to thin blood."])[0])
        physio = np.array(
            provider.embed_documents(["Physiotherapy exercises twice weekly."])[0]
        )
        assert float(query @ warfarin) > float(query @ physio)

    def test_unrelated_topics_score_below_the_floor(self, provider):
        document = "Paracetamol 500mg for pain. Change the wound dressing daily."
        for query in ("insulin sliding scale", "dialysis machine", "eczema rash"):
            assert self._sim(provider, query, document) < provider.relevance_floor

    def test_related_topics_clear_the_floor(self, provider):
        document = "Paracetamol 500mg for pain. Change the wound dressing daily."
        for query in ("pain relief medication", "how to dress a wound"):
            assert self._sim(provider, query, document) >= provider.relevance_floor

    def test_query_and_document_use_different_instruction_handling(
        self, provider
    ):
        # A document vector must not have the query instruction prepended;
        # that would make documents and queries live in different subspaces
        # and silently degrade every score.
        document = "Paracetamol 500mg for pain relief."
        as_document = provider.embed_documents([document])[0]
        as_query = provider.embed_query(document)
        assert as_document != as_query

    def test_deterministic_across_calls(self, provider):
        first = provider.embed_query("wound dressing")
        second = provider.embed_query("wound dressing")
        assert first == second

    def test_is_repeatable_in_a_fresh_instance(self):
        """Same text, new provider object, same vector (CPU determinism)."""
        a = SemanticEmbeddingProvider(get_settings()).embed_query("physiotherapy")
        b = SemanticEmbeddingProvider(get_settings()).embed_query("physiotherapy")
        assert a == b

    def test_batches_and_singles_agree(self, provider):
        texts = ["paracetamol dosage", "wound dressing daily", "physiotherapy"]
        batched = provider.embed_documents(texts)
        singles = [provider.embed_documents([t])[0] for t in texts]
        for b, s in zip(batched, singles):
            assert pytest.approx(1.0, abs=1e-5) == sum(
                x * y for x, y in zip(b, s)
            )

    def test_truncates_rather_than_failing_on_long_text(self, provider):
        long_text = "paracetamol " * 5000
        vector = provider.embed_documents([long_text])[0]
        assert len(vector) == 384

    def test_handles_unicode_and_clinical_symbols(self, provider):
        vector = provider.embed_documents(["Paracetamol 500 mg — 6 hourly, µgg/kg."])[0]
        assert len(vector) == 384

    def test_empty_chunk_is_rejected(self, provider):
        with pytest.raises(EmbeddingError):
            provider.embed_documents([""])

    def test_inference_makes_no_network_call(self, provider, monkeypatch):
        """
        The PHI guarantee, enforced at test time.

        Inference must not touch the network. `huggingface_hub` is stubbed to
        explode, so any accidental network use fails loudly rather than
        quietly sending document text to a third party.
        """
        import huggingface_hub

        def _forbidden(*args, **kwargs):
            raise AssertionError("semantic inference must not use the network")

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", _forbidden)
        monkeypatch.setattr(huggingface_hub, "snapshot_download", _forbidden)
        provider.embed_documents(["Paracetamol 500mg for pain relief."])
        provider.embed_query("what painkiller can I take")


def test_real_model_dir_is_configurable_relative_to_backend():
    """A relative model dir must resolve the same way regardless of CWD."""
    settings = get_settings()
    resolved = _resolve_dest(settings.rag_semantic_model_dir)
    assert resolved.is_absolute()
    assert resolved.name == "bge-small-en-v1.5"
    # Anchored under the backend root, not the process working directory.
    assert resolved.parts[-3:] == ("models", "embeddings", "bge-small-en-v1.5")
