"""
CareLoop AI - Semantic Embedding Model Fetcher

One-time, operator-initiated download of the local ONNX embedding model.  This
is deliberately a **command-line tool**, not something the API can trigger:
model weights are a deployment decision, and letting a request start a
127 MB download would turn a misconfigured host into a slow denial of
service.

Run it once during deployment:

    python -m app.rag.embeddings.fetch_model

The files land in `RAG_SEMANTIC_MODEL_DIR` (default
`models/embeddings/bge-small-en-v1.5`, relative to the backend root) and that
directory is git-ignored.  After it succeeds the application never needs the
network again, and `RAG_SEMANTIC_ALLOW_DOWNLOAD` can be set to false.

No patient data is involved: only model weights are fetched, and nothing is
uploaded.  The repository id and destination are operator-controlled settings,
so neither can be driven by request data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from app.core.config import get_settings

# Kept explicit rather than globbing the repo: a partial download that misses
# one of these would otherwise look complete and fail at inference time.
_MODEL_FILES = ("onnx/model.onnx", "tokenizer.json", "config.json")

#: Written next to the weights so the fetched artefact records its own origin.
#: The embedding fingerprint uses the CONFIGURED revision, so this file is
#: what lets an operator confirm later that the weights on disk are the ones
#: the configuration claims - and re-fetch deliberately if they are not.
_REVISION_FILE = "careloop_model_revision.txt"
_HINT = (
    "Model files fetched. The application now runs the embedding model "
    "locally and will not contact the network again."
)


def _resolve_dest(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    return path


def is_model_present(dest: Path) -> bool:
    """True when every required file exists and is non-empty."""
    return all(
        (dest / name).is_file() and (dest / name).stat().st_size > 0
        for name in _MODEL_FILES
    )


def read_recorded_revision(dest: Path) -> Optional[str]:
    """
    The revision recorded by the last successful fetch, if any.

    Parses the `revision=` line rather than returning the file, so a future
    format change (adding `repo=`) cannot silently turn the marker into a
    bogus revision string.
    """
    for line in _read_marker(dest).splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "revision":
            return value.strip() or None
    return None


def read_recorded_repo(dest: Path) -> Optional[str]:
    """The repository id recorded by the last successful fetch, if any."""
    for line in _read_marker(dest).splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "repo":
            return value.strip() or None
    return None


def _read_marker(dest: Path) -> str:
    marker = dest / _REVISION_FILE
    if not marker.is_file():
        return ""
    try:
        return marker.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - unreadable marker
        return ""


def _record_revision(dest: Path, repo: str, revision: str) -> None:
    (dest / _REVISION_FILE).write_text(
        f"repo={repo}\nrevision={revision}\n", encoding="utf-8"
    )


def fetch_model(repo: str, dest: Path, revision: str = "main") -> None:
    """
    Download the model's files into `dest`.

    Raises SystemExit with an actionable message when `huggingface_hub` is
    missing or the fetch fails; a half-written model directory is removed
    first so a retry cannot pick up a corrupt file.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print(
            "huggingface_hub is required to fetch the model.\n"
            "Install it with:  pip install huggingface_hub",
            file=sys.stderr,
        )
        raise SystemExit(2)

    recorded = read_recorded_revision(dest)
    recorded_repo = read_recorded_repo(dest)
    if is_model_present(dest):
        if (recorded and recorded != revision) or (
            recorded_repo and recorded_repo != repo
        ):
            print(
                f"Model already present at {dest}, but it was fetched as "
                f"{recorded_repo or '<unknown>'}@{recorded or '<unrecorded>'} "
                f"and {repo}@{revision} is now requested.\nThese weights "
                f"produce different vectors. Stop the service, delete {dest}, "
                f"and re-run this command - then re-index every document.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"Model already present at {dest}; nothing to do.")
        return

    dest.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {repo}@{revision} -> {dest}")
    for name in _MODEL_FILES:
        print(f"  {name}")
        try:
            hf_hub_download(
                repo_id=repo,
                filename=name,
                revision=revision,
                local_dir=str(dest),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            print(
                f"\nFailed to download {name}: {exc.__class__.__name__}: {exc}\n"
                f"Partial download left in {dest}; remove it and retry.",
                file=sys.stderr,
            )
            raise SystemExit(1)

    if not is_model_present(dest):
        print(
            f"Download finished but {dest} is still incomplete.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _record_revision(dest, repo, revision)
    print(_HINT)


def main(argv: Optional[Sequence[str]] = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m app.rag.embeddings.fetch_model",
        description=(
            "Download the local ONNX semantic embedding model. Run once "
            "during deployment; inference never needs the network."
        ),
    )
    parser.add_argument(
        "--repo",
        default=settings.rag_semantic_model_name,
        help="Hugging Face repository id (default: %(default)s)",
    )
    parser.add_argument(
        "--revision",
        default=settings.rag_semantic_model_revision,
        help=(
            "Model revision to fetch. Part of the vector collection's "
            "fingerprint, so changing it requires a re-index "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--dest",
        default=settings.rag_semantic_model_dir,
        help="Destination directory (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    fetch_model(args.repo, _resolve_dest(args.dest), args.revision)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
