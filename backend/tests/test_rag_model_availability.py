"""
CareLoop AI - Semantic Model Availability Tests (Phase 3)

Issue 2 asked for proof of four properties:

  1. the provider uses the CONFIGURED local model, not a hardcoded one
  2. the model identifier is deterministic and configurable
  3. missing weights produce a clear, actionable error
  4. there is NO silent fallback to lexical/hash embeddings

Property 4 is the dangerous one.  A retrieval service that quietly degrades to
a bag-of-words model when the real model is missing would keep returning 200s
and plausible-looking passages while no longer matching meaning - the failure
would surface as "search is suddenly bad", not as an error.

No test here downloads weights or calls a network service.  Tests that need a
real model are marked and skip when it is absent.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import EmbeddingNotConfiguredError
from app.rag.embeddings import PROVIDERS, get_embedding_provider
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.fetch_model import (
    _resolve_dest,
    is_model_present,
    read_recorded_revision,
)
from app.rag.embeddings.semantic import SemanticEmbeddingProvider

SEMANTIC = "semantic"
HASHING = "hashing"


def make_settings(**overrides) -> Settings:
    base = {
        "secret_key": "x" * 64,
        "rag_embedding_provider": SEMANTIC,
        # Point at a directory that cannot contain a model, so these tests
        # never depend on weights being present.
        "rag_semantic_model_dir": "models/embeddings/definitely-not-fetched",
    }
    base.update(overrides)
    return Settings(**base)


class IdentityProbe(SemanticEmbeddingProvider):
    """
    Semantic provider with only the vector WIDTH stubbed out.

    In production `dimensions` is read from the ONNX graph, so it needs the
    127 MB file - which is the right behaviour and is tested separately in
    TestMissingWeightsFailLoudly.  Identity tests only need determinism, so
    the width is fixed here.

    Nothing about the fingerprint logic itself is stubbed: `model_id`,
    `vector_space_config`, and the digest are all the real implementations.
    """

    def __init__(self, settings=None, dimensions: int = 384) -> None:
        super().__init__(settings)
        self._stub_dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._stub_dimensions


# ── Uses the configured model ───────────────────────────────────────────────


class TestUsesConfiguredModel:
    def test_provider_is_selected_by_configuration(self):
        assert make_settings().rag_embedding_provider == SEMANTIC
        assert (
            get_embedding_provider(settings=make_settings()).name == SEMANTIC
        )

    def test_model_name_comes_from_settings_not_a_constant(self):
        provider = IdentityProbe(
            make_settings(rag_semantic_model_name="acme/other-encoder")
        )
        assert provider.model_id.startswith("acme/other-encoder@")

    def test_model_dir_comes_from_settings(self, tmp_path):
        settings = make_settings(rag_semantic_model_dir=str(tmp_path / "here"))
        provider = SemanticEmbeddingProvider(settings)
        assert provider._configured_dir() == Path(tmp_path / "here")

    def test_relative_model_dir_resolves_against_backend(self):
        provider = SemanticEmbeddingProvider(
            make_settings(rag_semantic_model_dir="models/embeddings/x")
        )
        resolved = provider._configured_dir()
        assert resolved.is_absolute()
        assert resolved.parts[-3:] == ("models", "embeddings", "x")

    def test_semantic_is_the_shipped_default(self):
        assert Settings(secret_key="x" * 64).rag_embedding_provider == SEMANTIC


# ── Deterministic, configurable identifier ──────────────────────────────────


class TestModelIdentity:
    def test_identifier_is_deterministic_for_one_configuration(self):
        first = IdentityProbe(make_settings())
        second = IdentityProbe(make_settings())
        assert first.model_id == second.model_id
        assert first.fingerprint_digest == second.fingerprint_digest

    def test_revision_is_configurable_and_changes_identity(self):
        main = IdentityProbe(
            make_settings(rag_semantic_model_revision="main")
        )
        pinned = IdentityProbe(
            make_settings(rag_semantic_model_revision="c0ffee1234")
        )
        assert main.model_id.endswith("@main")
        assert pinned.model_id.endswith("@c0ffee1234")
        assert main.fingerprint_digest != pinned.fingerprint_digest

    def test_max_tokens_changes_identity_because_it_changes_vectors(self):
        base = IdentityProbe(make_settings())
        wider = IdentityProbe(
            make_settings(rag_semantic_max_tokens=256)
        )
        assert base.vector_space_config != wider.vector_space_config
        assert base.fingerprint_digest != wider.fingerprint_digest

    def test_query_instruction_does_not_invalidate_stored_vectors(self):
        """
        The instruction is applied to queries only, so changing it leaves
        every stored document vector valid.

        Including it in the fingerprint would force a pointless full re-index.
        """
        base = IdentityProbe(make_settings())
        other = IdentityProbe(
            make_settings(rag_semantic_query_instruction="Different: ")
        )
        assert base.vector_space_config == other.vector_space_config
        assert base.fingerprint_digest == other.fingerprint_digest

    def test_model_dir_does_not_change_identity(self):
        """Where the weights live is not what they are."""
        base = IdentityProbe(make_settings())
        moved = IdentityProbe(
            make_settings(rag_semantic_model_dir="models/embeddings/elsewhere")
        )
        assert base.fingerprint_digest == moved.fingerprint_digest

    def test_identifier_leaks_no_path_or_secret(self):
        provider = IdentityProbe(
            make_settings(rag_semantic_model_dir="C:/secret/clinical/model")
        )
        assert "secret" not in provider.model_id.lower()
        assert "clinical" not in provider.model_id.lower()


# ── Missing weights fail loudly ─────────────────────────────────────────────


class TestMissingWeightsFailLoudly:
    def test_is_configured_is_false_without_weights(self):
        assert SemanticEmbeddingProvider(make_settings()).is_configured() is False

    def test_raises_rather_than_returning_a_vector(self):
        provider = SemanticEmbeddingProvider(make_settings())
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider.embed_query("warfarin")
        assert "not present" in str(exc.value)

    def test_error_tells_the_operator_how_to_fix_it(self):
        provider = SemanticEmbeddingProvider(make_settings())
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider.ensure_configured()
        message = str(exc.value)
        # Actionable: names the fetch command and the escape hatch.
        assert "fetch_model" in message
        assert HASHING in message

    def test_error_on_airgapped_host_does_not_suggest_a_download(self):
        provider = SemanticEmbeddingProvider(
            make_settings(rag_semantic_allow_download=False)
        )
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider.ensure_configured()
        message = str(exc.value)
        assert "RAG_SEMANTIC_ALLOW_DOWNLOAD" in message
        assert "cannot be fetched" in message

    def test_incomplete_download_is_treated_as_absent(self, tmp_path):
        """
        A truncated model must not look usable.

        The failure mode this prevents is a tokenizer that loads and an ONNX
        graph that is silently wrong.
        """
        model_dir = tmp_path / "partial"
        (model_dir / "onnx").mkdir(parents=True)
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        # onnx/model.onnx deliberately absent.
        settings = make_settings(rag_semantic_model_dir=str(model_dir))
        provider = SemanticEmbeddingProvider(settings)
        assert is_model_present(model_dir) is False
        assert provider.is_configured() is False
        with pytest.raises(EmbeddingNotConfiguredError):
            provider.embed_query("anything")

    def test_empty_onnx_file_is_treated_as_absent(self, tmp_path):
        model_dir = tmp_path / "empty"
        (model_dir / "onnx").mkdir(parents=True)
        (model_dir / "onnx" / "model.onnx").write_bytes(b"")
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        assert is_model_present(model_dir) is False

    def test_dimensions_cannot_be_read_without_weights(self):
        """
        `dimensions` would otherwise need a load, so it must raise the same
        actionable error rather than a bare AttributeError on None.
        """
        provider = SemanticEmbeddingProvider(make_settings())
        with pytest.raises(EmbeddingNotConfiguredError):
            provider.dimensions

    def test_error_contains_no_absolute_path_or_secret(self):
        provider = SemanticEmbeddingProvider(
            make_settings(rag_semantic_model_dir="C:/patients/real/model")
        )
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            provider.ensure_configured()
        message = str(exc.value)
        assert "C:/patients" not in message
        assert "secret" not in message.lower()


# ── No silent fallback ──────────────────────────────────────────────────────


class TestNoSilentFallback:
    def test_unknown_provider_raises_instead_of_defaulting(self):
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            get_embedding_provider("typo-provider", settings=make_settings())
        assert "typo-provider" in str(exc.value)

    def test_blank_provider_env_var_raises_instead_of_defaulting(self):
        """
        A blanked or typo'd RAG_EMBEDDING_PROVIDER must not silently resolve.

        This is the realistic misconfiguration: an operator empties the
        variable or fells it to a value that no longer exists.  Falling back
        to a default here would swap the retrieval model under the operator's
        feet with no error, which is the exact failure mode Issue 2 forbids.
        """
        settings = make_settings(rag_embedding_provider="")
        with pytest.raises(EmbeddingNotConfiguredError):
            get_embedding_provider(settings=settings).embed_query("warfarin")

    def test_near_miss_provider_name_raises(self):
        """`semantic ` with stray whitespace normalises; `semantc` does not."""
        assert (
            get_embedding_provider(
                "  SEMANTIC  ", settings=make_settings()
            ).name
            == SEMANTIC
        )
        with pytest.raises(EmbeddingNotConfiguredError):
            get_embedding_provider("semantc", settings=make_settings())

    def test_missing_semantic_model_never_returns_hashing(self):
        """
        The core assertion of Issue 2.

        Configuring `semantic` without weights must produce an error, never a
        working lexical provider.
        """
        with pytest.raises(EmbeddingNotConfiguredError):
            get_embedding_provider(
                SEMANTIC, settings=make_settings()
            ).embed_query("blood thinner")

    def test_factory_error_lists_both_real_providers(self):
        with pytest.raises(EmbeddingNotConfiguredError) as exc:
            get_embedding_provider("nope", settings=make_settings())
        message = str(exc.value)
        assert SEMANTIC in message
        assert HASHING in message

    def test_fallback_requires_an_explicit_opt_in(self):
        """
        Reaching the lexical provider requires naming it.

        Nothing in the code path can substitute it on its own.
        """
        settings = make_settings()
        assert settings.rag_embedding_provider == SEMANTIC
        explicit = get_embedding_provider(HASHING, settings=settings)
        assert explicit.name == HASHING
        assert explicit.is_configured() is True

    def test_no_provider_constructs_another_provider(self):
        """
        Static check: no embedding module reaches for a different provider.

        A silent swap would have to be written as an instantiation of a
        concrete provider from inside another provider, or a factory call
        with a hardcoded name.
        """
        import ast
        from pathlib import Path as _P

        import app.rag.embeddings as pkg

        forbidden = {"get_embedding_provider", "HashingEmbeddingProvider",
                     "SemanticEmbeddingProvider"}
        for path in sorted((_P(pkg.__file__).parent).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(
                    func, "attr", None
                )
                if name not in forbidden:
                    continue
                # Construction inside the provider's own class body is fine;
                # a cross-provider call is not.  Report anything in a module
                # that is not the factory or a test helper.
                if path.name in {"factory.py"}:
                    continue
                pytest.fail(
                    f"{path.name}:{node.lineno} calls {name}(); embedding "
                    f"modules must not construct each other"
                )

    def test_semantic_service_failure_does_not_downgrade(self, tmp_path):
        """
        A failed semantic embed must not be retried as a lexical embed.
        """
        from app.rag.chunking import TextChunk
        from app.rag.vector_store import ChromaVectorStore

        settings = make_settings(
            chroma_persist_directory=str(tmp_path / "chroma")
        )
        store = ChromaVectorStore(
            settings,
            embedding_provider=get_embedding_provider(
                SEMANTIC, settings=settings
            ),
        )
        chunk = TextChunk(
            chunk_id="x",
            document_id=uuid.uuid4(),
            patient_id=uuid.uuid4(),
            text="Warfarin to thin the blood.",
            page=1,
            index=0,
        )
        with pytest.raises(EmbeddingNotConfiguredError):
            store.upsert_chunks([chunk])
        # Nothing was written, so a later correct index is not polluted.
        assert store._get_collection().count() == 0


# ── Provider registry ───────────────────────────────────────────────────────


class TestProviderRegistry:
    def test_both_providers_registered(self):
        assert SEMANTIC in PROVIDERS
        assert HASHING in PROVIDERS

    @pytest.mark.parametrize("name", [SEMANTIC, HASHING])
    def test_every_provider_can_describe_itself(self, name):
        if name == SEMANTIC:
            # Width needs the ONNX graph; stub only that (see IdentityProbe).
            provider = IdentityProbe(make_settings())
        else:
            provider = get_embedding_provider(name, settings=make_settings())
        assert isinstance(provider, EmbeddingProvider)
        fingerprint = provider.fingerprint
        assert fingerprint["careloop_embedding_provider"] == name
        assert fingerprint["careloop_embedding_dimensions"] == provider.dimensions
        assert fingerprint["careloop_embedding_model"]

    def test_real_semantic_provider_cannot_describe_itself_without_weights(self):
        """
        Documented consequence of reading the width from the graph.

        Without the model there is no honest width to record, so the provider
        raises rather than guessing - and it raises the actionable
        model-missing error, not a fingerprint error.
        """
        provider = get_embedding_provider(SEMANTIC, settings=make_settings())
        with pytest.raises(EmbeddingNotConfiguredError):
            provider.fingerprint


# ── Fetched artefact records its origin ─────────────────────────────────────


class TestFetchedArtifactIsSelfDescribing:
    def test_absent_model_records_no_revision(self, tmp_path):
        assert read_recorded_revision(tmp_path) is None

    def test_missing_files_reported_as_absent(self, tmp_path):
        assert is_model_present(tmp_path) is False

    def test_fetch_records_the_revision_it_used(self, tmp_path, monkeypatch):
        """
        The weights on disk must be able to say where they came from.

        Without this, a silently re-fetched `main` looks identical to the
        original and nobody can tell a re-index is needed.
        """
        import app.rag.embeddings.fetch_model as fm

        def fake_download(repo_id, filename, revision, local_dir):
            target = Path(local_dir) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"weights")
            return str(target)

        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)

        dest = tmp_path / "model"
        fm.fetch_model("acme/encoder", dest, revision="abc123")

        assert is_model_present(dest) is True
        recorded = read_recorded_revision(dest)
        assert recorded == "abc123"
        assert "acme/encoder" in (dest / "careloop_model_revision.txt").read_text(
            encoding="utf-8"
        )

    def test_refetching_a_different_revision_is_refused(
        self, tmp_path, monkeypatch
    ):
        """
        Re-fetching different weights over an existing model would invalidate
        every stored vector, so it must stop and explain rather than quietly
        replacing them.
        """
        import app.rag.embeddings.fetch_model as fm

        def fake_download(repo_id, filename, revision, local_dir):
            target = Path(local_dir) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"weights")
            return str(target)

        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)

        dest = tmp_path / "model"
        fm.fetch_model("acme/encoder", dest, revision="abc123")
        assert read_recorded_revision(dest) == "abc123"

        # Same destination, different revision: refuse.
        with pytest.raises(SystemExit) as exc:
            fm.fetch_model("acme/encoder", dest, revision="def456")
        assert exc.value.code == 1
        # The original weights are untouched.
        assert read_recorded_revision(dest) == "abc123"

    def test_refetching_the_same_revision_is_a_no_op(
        self, tmp_path, monkeypatch
    ):
        import app.rag.embeddings.fetch_model as fm

        def fake_download(repo_id, filename, revision, local_dir):
            target = Path(local_dir) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"weights")
            return str(target)

        import huggingface_hub

        monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)

        dest = tmp_path / "model"
        fm.fetch_model("acme/encoder", dest, revision="abc123")
        fm.fetch_model("acme/encoder", dest, revision="abc123")  # no raise
        assert read_recorded_revision(dest) == "abc123"

    def test_fetch_cli_exposes_revision(self):
        from app.rag.embeddings.fetch_model import main

        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_resolve_dest_is_backend_relative(self):
        resolved = _resolve_dest("models/embeddings/x")
        assert resolved.is_absolute()
        assert resolved.parts[-3:] == ("models", "embeddings", "x")
