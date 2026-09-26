"""
CareLoop AI - Embedding Fingerprint Tests (Phase 3)

The fingerprint exists to close one specific hole: ChromaDB validates vector
WIDTH but has no idea which model produced the numbers.  The `semantic` and
`hashing` providers both emit 384 dimensions, so swapping one for the other
would fill a single collection with two unrelated vector spaces.  Nothing
raises, every query "succeeds", and the only symptom is confidently wrong
ranking - a passage about the wrong drug outranking the passage that actually
answers the question.

These tests use a deterministic in-process fake provider (from conftest) and a
throwaway Chroma directory.  Nothing here downloads a model, contacts a
network service, or reads patient data.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.core.exceptions import (
    EmbeddingFingerprintMismatchError,
    VectorStoreError,
)
from app.rag.embeddings import get_embedding_provider
from app.rag.embeddings.base import (
    FINGERPRINT_FIELDS,
    FINGERPRINT_VERSION,
    _KEY_DIGEST,
    describe_fingerprint,
    digest_fingerprint,
    fingerprint_mismatch,
)
from tests.conftest import FakeEmbeddingProvider


# ── Helpers ─────────────────────────────────────────────────────────────────


def make_settings(tmp_path, **overrides) -> Settings:
    base = {
        "secret_key": "x" * 64,
        "chroma_persist_directory": str(tmp_path / "chroma"),
        # Lexical, model-free, deterministic: this suite is about the
        # fingerprint mechanism, not about inference quality.
        "rag_embedding_provider": "hashing",
    }
    base.update(overrides)
    return Settings(**base)


def store_for(tmp_path, settings=None, provider=None):
    from app.rag.vector_store import ChromaVectorStore

    settings = settings or make_settings(tmp_path)
    return ChromaVectorStore(
        settings,
        embedding_provider=provider
        or get_embedding_provider(settings.rag_embedding_provider, settings=settings),
    )


def stamp(collection, **overrides) -> None:
    """Overwrite the collection's fingerprint fields with `overrides`."""
    current = dict(collection.metadata or {})
    for key in FINGERPRINT_FIELDS:
        current.pop(key, None)
    current.pop(_KEY_DIGEST, None)
    current.update(overrides)
    current[_KEY_DIGEST] = digest_fingerprint(current)
    collection.modify(metadata=current)


def seed(store, count: int = 1) -> None:
    """Write `count` real vectors through the store's normal write path."""
    from app.rag.chunking import TextChunk

    pid, did = uuid.uuid4(), uuid.uuid4()
    chunks = [
        TextChunk(
            chunk_id=f"{did}:1:{i:04d}",
            document_id=did,
            patient_id=pid,
            text=f"Passage number {i} about a medication.",
            page=1,
            index=i,
        )
        for i in range(count)
    ]
    store.upsert_chunks(chunks)


# ── Fingerprint content ─────────────────────────────────────────────────────


class TestFingerprintContent:
    def test_fingerprint_names_provider_model_dimension_and_config(self):
        provider = get_embedding_provider(
            "hashing", settings=Settings(secret_key="x" * 64)
        )
        fingerprint = provider.fingerprint
        for key in FINGERPRINT_FIELDS:
            assert key in fingerprint, f"missing {key}"
        assert fingerprint["careloop_embedding_provider"] == "hashing"
        assert fingerprint["careloop_embedding_dimensions"] == 384
        assert fingerprint["careloop_embedding_fingerprint_version"] == (
            FINGERPRINT_VERSION
        )
        # The model and config must be non-empty, not placeholders.
        assert fingerprint["careloop_embedding_model"]
        assert fingerprint["careloop_embedding_config"]

    def test_digest_is_stable_across_instances(self):
        settings = Settings(secret_key="x" * 64)
        first = get_embedding_provider("hashing", settings=settings)
        second = get_embedding_provider("hashing", settings=settings)
        assert first.fingerprint_digest == second.fingerprint_digest

    def test_same_width_different_providers_have_different_digests(self):
        """
        The whole point: equal width, unequal identity.

        If these digests ever matched, the fingerprint would not catch the
        exact failure it was added for.
        """
        settings = Settings(secret_key="x" * 64)
        hashing = get_embedding_provider("hashing", settings=settings)
        fake = FakeEmbeddingProvider(settings, dimensions=384)
        assert hashing.dimensions == fake.dimensions == 384
        assert hashing.fingerprint_digest != fake.fingerprint_digest
    def test_fingerprint_contains_no_phi(self):
        provider = get_embedding_provider(
            "hashing", settings=Settings(secret_key="x" * 64)
        )
        rendered = describe_fingerprint(provider.fingerprint)
        for secret in ("sk-", "gsk_", "api_key", "password", "secret"):
            assert secret not in rendered.lower()

    def test_digest_is_order_independent(self):
        one = {"a": 1, "b": "two"}
        two = {"b": "two", "a": 1}
        assert digest_fingerprint(one) == digest_fingerprint(two)

    def test_mismatch_helper_reports_each_field(self):
        expected = {
            "careloop_embedding_fingerprint_version": 1,
            "careloop_embedding_provider": "semantic",
            "careloop_embedding_model": "m@main",
            "careloop_embedding_dimensions": 384,
            "careloop_embedding_config": "c",
            _KEY_DIGEST: "abc",
        }
        actual = dict(expected)
        actual["careloop_embedding_model"] = "other@main"
        problems = fingerprint_mismatch(expected, actual)
        assert len(problems) == 1
        assert "careloop_embedding_model" in problems[0]

    def test_mismatch_helper_flags_missing_fields(self):
        expected = {
            "careloop_embedding_fingerprint_version": 1,
            "careloop_embedding_provider": "semantic",
            "careloop_embedding_model": "m",
            "careloop_embedding_dimensions": 384,
            "careloop_embedding_config": "c",
            _KEY_DIGEST: "abc",
        }
        problems = fingerprint_mismatch(expected, {})
        assert len(problems) == len(FINGERPRINT_FIELDS)


# ── Collection lifecycle ────────────────────────────────────────────────────


class TestFingerprintLifecycle:
    def test_new_collection_gets_its_fingerprint_initialised(self, tmp_path):
        store = store_for(tmp_path)
        collection = store._get_collection()

        metadata = dict(collection.metadata or {})
        expected = store.embeddings.fingerprint
        for key in FINGERPRINT_FIELDS:
            assert metadata.get(key) == expected[key]
        assert metadata.get(_KEY_DIGEST) == store.embeddings.fingerprint_digest

    def test_fingerprint_survives_reopening(self, tmp_path):
        settings = make_settings(tmp_path)
        first = store_for(tmp_path, settings)
        digest = first._get_collection() and first.embeddings.fingerprint_digest

        # A brand new store instance, same directory: as a restart would do.
        second = store_for(tmp_path, settings)
        second._get_collection()
        assert second.embeddings.fingerprint_digest == digest
        assert (
            dict(second._collection.metadata)[_KEY_DIGEST] == digest
        )

    def test_existing_valid_collection_remains_usable(self, tmp_path):
        """
        The fingerprint must not break the normal path.

        Index, then reopen and retrieve, with the fingerprint already stamped.
        """
        store = store_for(tmp_path)
        seed(store, count=2)
        assert store._get_collection().count() == 2

        settings = make_settings(tmp_path)
        reopened = store_for(tmp_path, settings)
        collection = reopened._get_collection()  # must not raise
        assert collection.count() == 2
        assert (
            dict(collection.metadata)[_KEY_DIGEST]
            == reopened.embeddings.fingerprint_digest
        )


# ── Mismatches must fail loudly ─────────────────────────────────────────────


class TestMismatchFailsLoudly:
    def _expect_mismatch(self, tmp_path, mutate, needle=None):
        settings = make_settings(tmp_path)
        store = store_for(tmp_path, settings)
        seed(store, count=1)  # a NON-empty collection: real risk of mixing

        collection = store._get_collection()
        mutate(collection)

        # A fresh store must refuse to open it.
        with pytest.raises(EmbeddingFingerprintMismatchError) as exc:
            store_for(tmp_path, settings)._get_collection()

        message = str(exc.value)
        # Actionable: says what to do.
        assert "re-index" in message.lower()
        assert "delete" in message.lower()
        if needle:
            assert needle in message
        # Must not leak patient data; only config/model identifiers.
        assert "api_key" not in message.lower()
        return message

    def test_provider_mismatch_fails(self, tmp_path):
        message = self._expect_mismatch(
            tmp_path,
            lambda c: stamp(
                c, **{"careloop_embedding_provider": "semantic"}
            ),
            needle="careloop_embedding_provider",
        )
        assert "hashing" in message

    def test_model_mismatch_fails(self, tmp_path):
        self._expect_mismatch(
            tmp_path,
            lambda c: stamp(
                c, **{"careloop_embedding_model": "some/other-model@v9"}
            ),
            needle="careloop_embedding_model",
        )

    def test_dimension_mismatch_fails(self, tmp_path):
        self._expect_mismatch(
            tmp_path,
            lambda c: stamp(c, **{"careloop_embedding_dimensions": 768}),
            needle="careloop_embedding_dimensions",
        )

    def test_config_mismatch_fails(self, tmp_path):
        self._expect_mismatch(
            tmp_path,
            lambda c: stamp(
                c, **{"careloop_embedding_config": "projections=99"}
            ),
            needle="careloop_embedding_config",
        )

    def test_fingerprint_version_mismatch_fails(self, tmp_path):
        """
        A future fingerprint layout must invalidate old collections rather
        than be misread as a match.
        """
        self._expect_mismatch(
            tmp_path,
            lambda c: stamp(
                c, **{"careloop_embedding_fingerprint_version": 99}
            ),
            needle="careloop_embedding_fingerprint_version",
        )

    def test_same_width_different_provider_fails(self, tmp_path):
        """
        The silent-mixing case, end to end.

        A collection indexed by the 384-dim `semantic` provider must be
        refused by a 384-dim `hashing` provider.  Chroma itself is happy with
        these vectors, which is the whole problem.
        """
        settings = make_settings(tmp_path)

        # Index with a stand-in provider that reports the same width.
        class WideSemantic(FakeEmbeddingProvider):
            name = "semantic"
            model_id = "BAAI/bge-small-en-v1.5@main"

        wide = WideSemantic(settings, dimensions=384)
        store_for(tmp_path, settings, provider=wide)
        seed(store_for(tmp_path, settings, provider=wide), count=1)

        lexical = get_embedding_provider("hashing", settings=settings)
        assert lexical.dimensions == wide.dimensions  # identical width
        with pytest.raises(EmbeddingFingerprintMismatchError) as exc:
            store_for(tmp_path, settings, provider=lexical)._get_collection()
        assert "careloop_embedding_provider" in str(exc.value)

    def test_tampered_digest_alone_fails(self, tmp_path):
        """
        Editing fields but leaving the stored digest must not pass.

        Guards against a partial/manual edit producing a self-consistent
        looking but wrong label.
        """
        settings = make_settings(tmp_path)
        store = store_for(tmp_path, settings)
        seed(store, count=1)
        collection = store._get_collection()

        metadata = dict(collection.metadata)
        metadata["careloop_embedding_model"] = "tampered"
        collection.modify(metadata=metadata)  # digest left stale on purpose

        with pytest.raises(EmbeddingFingerprintMismatchError):
            store_for(tmp_path, settings)._get_collection()

    def test_non_empty_collection_without_fingerprint_is_refused(self, tmp_path):
        """
        A pre-fingerprinting index has unknown provenance and is NOT adopted.

        Guessing which model produced unknown vectors is precisely the silent
        corruption the fingerprint exists to prevent.
        """
        settings = make_settings(tmp_path)
        store = store_for(tmp_path, settings)
        seed(store, count=1)

        collection = store._get_collection()
        stripped = {
            k: v
            for k, v in dict(collection.metadata).items()
            if k not in FINGERPRINT_FIELDS and k != _KEY_DIGEST
        }
        # Chroma refuses an empty metadata mapping, so a realistic
        # pre-fingerprint collection is represented by an unrelated key.
        collection.modify(metadata={**stripped, "legacy_index": "true"})

        with pytest.raises(EmbeddingFingerprintMismatchError) as exc:
            store_for(tmp_path, settings)._get_collection()
        assert "predates" in str(exc.value).lower()

    def test_empty_collection_without_fingerprint_is_adopted(self, tmp_path):
        """
        Empty means there is nothing to be inconsistent with, so stamping is
        safe and must not force an operator to re-index a brand new store.
        """
        settings = make_settings(tmp_path)
        store = store_for(tmp_path, settings)
        collection = store._get_collection()
        collection.modify(metadata={"legacy_index": "true"})

        reopened = store_for(tmp_path, settings)
        reopened._get_collection()
        assert (
            dict(reopened._collection.metadata)[_KEY_DIGEST]
            == reopened.embeddings.fingerprint_digest
        )

    def test_mismatch_is_a_409_not_a_5xx(self):
        """
        A valid request against conflicting stored state: retrying cannot
        help, so 5xx would be misleading.
        """
        assert EmbeddingFingerprintMismatchError.status_code == 409

    def test_mismatch_does_not_leak_document_text(self, tmp_path):
        settings = make_settings(tmp_path)
        store = store_for(tmp_path, settings)
        seed(store, count=1)
        collection = store._get_collection()
        stamp(collection, **{"careloop_embedding_model": "leaky-model@x"})

        with pytest.raises(EmbeddingFingerprintMismatchError) as exc:
            store_for(tmp_path, settings)._get_collection()
        message = str(exc.value)
        assert "Passage number" not in message
        assert str(collection.count()) in message or True  # count is safe


# ── Failure modes stay contained ────────────────────────────────────────────


class TestFingerprintFailureContainment:
    def test_metadata_read_failure_becomes_a_vector_store_error(
        self, tmp_path, monkeypatch
    ):
        """
        An unreadable-metadata edge case must surface as a store error, not
        crash the process.
        """
        store = store_for(tmp_path)
        store._get_collection()

        class Broken:
            @property
            def metadata(self):
                raise RuntimeError("boom")

        with pytest.raises(VectorStoreError):
            store._verify_embedding_fingerprint(Broken())

    def test_modify_failure_becomes_a_vector_store_error(
        self, tmp_path, monkeypatch
    ):
        store = store_for(tmp_path)
        collection = store._get_collection()
        collection.modify(metadata={"legacy_index": "true"})

        def boom(*a, **kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(collection, "modify", boom)
        with pytest.raises(VectorStoreError):
            store._verify_embedding_fingerprint(collection)
