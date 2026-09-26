"""
CareLoop AI - Local Semantic Embedding Provider (Phase 3)

Runs a real sentence-embedding model **on the host** through ONNX Runtime, so
retrieval matches meaning rather than wording: a query for "blood thinner"
finds a document that only ever says "warfarin ... to thin your blood".

Why ONNX Runtime and not sentence-transformers
----------------------------------------------
`torch` is ~2.5 GB installed and is not something a clinical backend should
drag in for inference.  ONNX Runtime is already a hard dependency of
`chromadb`, and `tokenizers` ships with it, so this provider adds **no new
dependency at all** - it reuses what the vector store already needs.  A
33M-parameter model then runs on CPU in a few milliseconds per chunk, which
is far below the cost of the extraction pipeline that produced the text.

PHI BOUNDARY
------------
Inference is entirely local: the model is loaded from a directory on disk and
text never leaves the process.  There is no embedding API, no API key, and no
outbound call on the request path.  `rag_semantic_allow_download` governs a
single *operator-initiated* one-time fetch of the model weights from Hugging
Face; it is never consulted while serving a query, and can be set to false on
an air-gapped host.

DETERMINISM
-----------
The same text always yields the same vector, so re-indexing is reproducible.
Inference is deterministic on CPU; `onnxruntime` is only ever given the CPU
execution provider, because a GPU provider would make results depend on
hardware.

HEALTHCARE SAFETY BOUNDARY
--------------------------
An embedding is a numeric fingerprint of text a clinician already wrote.
Nothing here interprets, summarises, diagnoses, or rewrites it.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional, Sequence

from app.core.config import Settings
from app.core.exceptions import EmbeddingError, EmbeddingNotConfiguredError
from app.core.logging import get_logger
from app.rag.embeddings.base import EmbeddingProvider

logger = get_logger(__name__)

# Files that must be present for the provider to run.
_REQUIRED_FILES = ("tokenizer.json",)
_ONNX_SUBPATH = Path("onnx") / "model.onnx"

# Input names for the BERT-family graphs this provider targets.  Kept as
# constants so an unexpected graph fails with a clear message instead of an
# opaque onnxruntime error.
_INPUT_NAMES = ("input_ids", "attention_mask", "token_type_ids")
_OUTPUT_NAME = "last_hidden_state"

# ── Process-level model cache ────────────────────────────────────────────────
# FastAPI builds the RAG services (and therefore this provider) once PER
# REQUEST, so a per-instance session would re-read 127 MB off disk and rebuild
# the graph on every single retrieval.  The loaded artifacts are therefore
# shared process-wide, keyed by everything that changes the load.
#
# Sharing is safe: an onnxruntime InferenceSession is documented as safe to
# call `run()` on from multiple threads, and a HuggingFace `Tokenizer` with
# fixed padding/truncation is stateless during encode.  Both are immutable
# here, and the CPU execution provider makes results identical no matter how
# many threads call it.
_LOADED: dict[tuple[str, int, int], tuple[Any, Any, int]] = {}
_LOAD_LOCK = threading.Lock()


def _import_onnxruntime() -> Any:
    """Import onnxruntime lazily, with an actionable error if it is absent."""
    try:
        import onnxruntime  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise EmbeddingNotConfiguredError(
            "The semantic embedding provider needs the 'onnxruntime' package, "
            "which chromadb already depends on. Reinstall the backend "
            "requirements.",
            internal_detail=f"onnxruntime import failed: {exc.__class__.__name__}",
        ) from exc
    return onnxruntime


def _import_tokenizers() -> Any:
    """Import tokenizers lazily, with an actionable error if it is absent."""
    try:
        from tokenizers import Tokenizer  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise EmbeddingNotConfiguredError(
            "The semantic embedding provider needs the 'tokenizers' package, "
            "which chromadb already depends on. Reinstall the backend "
            "requirements.",
            internal_detail=f"tokenizers import failed: {exc.__class__.__name__}",
        ) from exc
    return Tokenizer


class SemanticEmbeddingProvider(EmbeddingProvider):
    """
    Local ONNX sentence embedder (default: BAAI/bge-small-en-v1.5).

    The model is loaded once, lazily, on first use and then shared.  Loading a
    127 MB graph takes far longer than a single query, so paying it per call
    would dominate latency; the session is also documented as safe to share
    across threads for `run()`.
    """

    name = "semantic"

    @property
    def model_id(self) -> str:
        """
        `repo@revision`: identifies the exact weights behind the vectors.

        Configurable and deterministic for a given configuration.  Including
        the revision matters because `main` is a moving target: re-fetching it
        after the upstream repository updates can yield different weights, and
        the fingerprint is what turns that silent incompatibility into a loud
        re-index requirement.
        """
        return (
            f"{self._settings.rag_semantic_model_name}"
            f"@{self._settings.rag_semantic_model_revision}"
        )

    @property
    def vector_space_config(self) -> str:
        """
        Settings that change the stored DOCUMENT vectors.

        `rag_semantic_query_instruction` is deliberately absent: it is applied
        to the query side only, so changing it alters query vectors but leaves
        every stored document vector valid.  Including it would force a
        pointless full re-index of the corpus.

        `rag_semantic_model_dir` is absent too: that is where the weights live,
        not what they are, and moving them on disk must not invalidate data.
        """
        return (
            f"max_tokens={self._settings.rag_semantic_max_tokens};"
            f"pooling=cls_norm"
        )

    #: Calibrated against a representative two-page discharge summary using
    #: the real chunker: 11/15 topically-relevant queries scored >= 0.60
    #: while 15/15 off-topic queries scored <= 0.59.  A dense model
    #: compresses cosine similarity into roughly 0.45-0.75 and never nears
    #: 0.0, which is why this floor is so much higher than the lexical
    #: provider's.
    default_min_score = 0.60

    def __init__(self, settings: Optional[Settings] = None) -> None:
        super().__init__(settings)
        # Lazily filled from the process-wide `_LOADED` cache; see _load().
        self._session: Any = None
        self._tokenizer: Any = None
        self._dimensions: Optional[int] = None
        self._model_path: Optional[Path] = None

    # ── Paths ───────────────────────────────────────────────────────────────

    def _configured_dir(self) -> Path:
        raw = self._settings.rag_semantic_model_dir.strip()
        path = Path(raw)
        if not path.is_absolute():
            # Anchored to the backend root, matching CHROMA_PERSIST_DIRECTORY,
            # so a relative path does not depend on the process CWD.
            path = Path(__file__).resolve().parents[3] / path
        return path

    def _onnx_path(self) -> Path:
        return self._configured_dir() / _ONNX_SUBPATH

    # ── Configuration ───────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """True when the local model files are present on disk."""
        directory = self._configured_dir()
        if not all((directory / name).is_file() for name in _REQUIRED_FILES):
            return False
        return self._onnx_path().is_file()

    def ensure_configured(self) -> None:
        """
        Raise a clear, actionable error when the model is missing.

        A missing model is a deployment problem, not a bad request, so the
        message says exactly which files are absent and how to obtain them -
        without echoing any path that could contain patient data (this one is
        operator-controlled, but the habit is worth keeping).
        """
        if self.is_configured():
            return
        directory = self._configured_dir()
        if self._settings.rag_semantic_allow_download:
            raise EmbeddingNotConfiguredError(
                f"The semantic embedding model is not present at "
                f"'{directory}'. Fetch it once with "
                f"`python -m app.rag.embeddings.fetch_model "
                f"--repo {self._settings.rag_semantic_model_name} "
                f"--dest {directory}`, or set RAG_EMBEDDING_PROVIDER=hashing "
                f"for a model-free (lexical) install.",
                internal_detail=(
                    f"missing semantic model at {directory}; "
                    f"allow_download={self._settings.rag_semantic_allow_download}"
                ),
            )
        raise EmbeddingNotConfiguredError(
            f"The semantic embedding model is not present at '{directory}' and "
            f"RAG_SEMANTIC_ALLOW_DOWNLOAD is disabled, so it cannot be fetched. "
            f"Copy the model files onto the host, or set "
            f"RAG_EMBEDDING_PROVIDER=hashing for a model-free install.",
            internal_detail=f"missing semantic model at {directory}",
        )

    # ── Model loading ───────────────────────────────────────────────────────

    def _load(self) -> None:
        """
        Build the tokenizer and ONNX session once PER PROCESS, under a lock.

        The cache is process-wide because this provider is constructed per
        request; see `_LOADED`.  The double check keeps a burst of concurrent
        first requests from each loading a 127 MB graph.
        """
        if self._session is not None:
            return
        with _LOAD_LOCK:
            if self._session is not None:
                return
            self.ensure_configured()

            onnxruntime = _import_onnxruntime()
            Tokenizer = _import_tokenizers()
            directory = self._configured_dir()
            onnx_file = self._onnx_path()

            max_tokens = self._settings.rag_semantic_max_tokens
            threads = int(self._settings.rag_semantic_num_threads or 0)
            key = (str(onnx_file), max_tokens, threads)

            cached = _LOADED.get(key)
            if cached is not None:
                self._tokenizer, self._session, self._dimensions = cached
                self._model_path = onnx_file
                logger.debug(
                    "Reusing cached semantic model: name=%s dimensions=%s",
                    self._settings.rag_semantic_model_name,
                    self._dimensions,
                )
                return

            try:
                tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
            except Exception as exc:
                raise EmbeddingError(
                    "The semantic embedding model's tokenizer could not be "
                    "read. The model download may be incomplete.",
                    internal_detail=(
                        f"tokenizer load failed at {directory}: "
                        f"{exc.__class__.__name__}"
                    ),
                ) from exc

            # Padding to the longest sequence in the batch: with a single fixed
            # width every short query would be padded to max_tokens and pay
            # full-length attention cost.
            tokenizer.enable_padding()
            tokenizer.enable_truncation(max_length=max_tokens)

            try:
                options = onnxruntime.SessionOptions()
                if threads > 0:
                    options.intra_op_num_threads = threads
                session = onnxruntime.InferenceSession(
                    str(onnx_file),
                    sess_options=options,
                    # CPU only. A GPU provider would make a vector depend on
                    # the hardware it ran on, breaking re-index
                    # reproducibility.
                    providers=["CPUExecutionProvider"],
                )
            except Exception as exc:
                raise EmbeddingError(
                    "The semantic embedding model could not be loaded. The "
                    "ONNX file may be corrupt or truncated.",
                    internal_detail=(
                        f"onnx session failed for {onnx_file}: "
                        f"{exc.__class__.__name__}"
                    ),
                ) from exc

            self._verify_graph(session)
            dimensions = int(session.get_outputs()[0].shape[-1])
            _LOADED[key] = (tokenizer, session, dimensions)
            self._tokenizer = tokenizer
            self._session = session
            self._dimensions = dimensions
            self._model_path = onnx_file
            logger.info(
                "Semantic embedding model loaded: name=%s dimensions=%s "
                "max_tokens=%s threads=%s",
                self._settings.rag_semantic_model_name,
                self._dimensions,
                max_tokens,
                threads or "auto",
            )

    def _verify_graph(self, session: Any) -> None:
        """
        Confirm the ONNX graph has the inputs and output this provider needs.

        A mismatched graph would otherwise fail deep inside `run()` with a
        node-level error that says nothing about which file is wrong.
        """
        inputs = {i.name for i in session.get_inputs()}
        missing = [name for name in _INPUT_NAMES if name not in inputs]
        if missing:
            raise EmbeddingNotConfiguredError(
                "The configured ONNX embedding model does not have the "
                "expected inputs, so it is not a supported sentence encoder. "
                f"Missing: {', '.join(missing)}.",
                internal_detail=f"onnx inputs={sorted(inputs)}",
            )
        outputs = [o.name for o in session.get_outputs()]
        if _OUTPUT_NAME not in outputs:
            raise EmbeddingNotConfiguredError(
                "The configured ONNX embedding model does not expose a "
                f"'{_OUTPUT_NAME}' output, so it is not a supported sentence "
                "encoder.",
                internal_detail=f"onnx outputs={outputs}",
            )

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._load()
        assert self._dimensions is not None  # narrowed by _load
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._settings.rag_semantic_model_name

    # ── Inference ───────────────────────────────────────────────────────────

    def _embed(self, texts: Sequence[str], *, is_query: bool) -> list[list[float]]:
        import numpy as np  # noqa: PLC0415 - local to keep import cost lazy

        self._load()
        assert self._session is not None and self._tokenizer is not None
        assert self._dimensions is not None

        prepared = list(texts)
        if is_query:
            # BGE models are trained with this instruction on the query side
            # only. Applying it to a document degrades the document vector.
            instruction = self._settings.rag_semantic_query_instruction
            if instruction:
                prepared = [instruction + text for text in prepared]

        encoded = self._tokenizer.encode_batch(prepared)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array(
            [e.attention_mask for e in encoded], dtype=np.int64
        )
        # Single-sequence classification graphs: every row is segment 0.
        token_type_ids = np.zeros_like(input_ids)

        try:
            hidden = self._session.run(
                [ _OUTPUT_NAME ],
                {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                },
            )[0]
        except Exception as exc:
            raise EmbeddingError(
                "The semantic embedding model failed while encoding text.",
                internal_detail=f"onnx run failed: {exc.__class__.__name__}",
            ) from exc

        # CLS pooling is what BGE was trained with; mean pooling would still
        # return a plausible vector of the right shape but would score
        # noticeably worse, which is exactly the kind of silent quality
        # regression this comment exists to prevent.
        vectors = hidden[:, 0]
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        # A zero row carries no direction; leave it rather than divide by 0.
        norms[norms == 0.0] = 1.0
        vectors = vectors / norms

        return self._validate(
            [[float(v) for v in row] for row in vectors],
            len(prepared),
            self._dimensions,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed document chunks. No query instruction is prepended."""
        if not texts:
            return []
        self._validate_texts(texts)
        return self._embed(texts, is_query=False)

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query, with the model's retrieval instruction."""
        if not text or not text.strip():
            raise EmbeddingError(
                "A blank query cannot be embedded.",
                internal_detail="empty query text",
            )
        vectors = self._embed([text], is_query=True)
        return vectors[0]

    @staticmethod
    def _validate_texts(texts: Sequence[str]) -> None:
        for text in texts:
            if not isinstance(text, str):
                raise EmbeddingError(
                    "Only text can be embedded.",
                    internal_detail=f"non-string input: {type(text).__name__}",
                )
            if not text.strip():
                raise EmbeddingError(
                    "A blank document chunk cannot be embedded.",
                    internal_detail="blank chunk text",
                )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        state = "loaded" if self._session is not None else "not-loaded"
        return (
            f"<{self.__class__.__name__} model={self.model_name!r} "
            f"state={state}>"
        )
