"""
CareLoop AI — Secure Document Storage Service (Phase 2)

Responsibilities:
  - validate uploads (extension, content type, magic bytes, size)
  - sanitise the client-supplied filename for DISPLAY only
  - generate UUID-based storage filenames
  - write bytes atomically outside the app/ package
  - compute SHA-256 for duplicate detection
  - resolve and delete stored files safely

SECURITY RULES:
- The uploaded filename is NEVER used to build a filesystem path.
- Path traversal sequences are stripped from the display name.
- Every read/write path is verified to remain inside the storage root.
- Storage never lives inside the `app/` Python package.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    DocumentValidationError,
    FileTooLargeError,
    StorageError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Allowed upload types ─────────────────────────────────────────────────────

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".png", ".jpg", ".jpeg"})

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
    }
)

# Extension -> canonical content type.
EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Leading magic bytes that must be present for each type.  A file whose
# extension says .pdf but whose bytes are not a PDF is rejected.
_MAGIC_SIGNATURES: tuple[tuple[bytes, tuple[str, ...]], ...] = (
    (b"%PDF-", (".pdf",)),
    (b"\x89PNG\r\n\x1a\n", (".png",)),
    (b"\xff\xd8\xff", (".jpg", ".jpeg")),
)

# Anything that looks like a path, drive letter, or shell metacharacter.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]")
_MULTI_DOT = re.compile(r"\.{2,}")


@dataclass(frozen=True)
class StoredDocument:
    """Result of a successful write to local storage."""

    stored_filename: str
    file_path: str
    file_size: int
    sha256_hash: str
    content_type: str
    original_filename: str


def _backend_root() -> Path:
    """Absolute path of the backend/ project root."""
    return Path(__file__).resolve().parents[2]


class DocumentStorageService:
    """Secure local storage for uploaded discharge documents."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._root = self._resolve_storage_root(self._settings.document_storage_path)

    # ── Paths ────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_storage_root(configured: str) -> Path:
        """
        Resolve DOCUMENT_STORAGE_PATH to an absolute directory.

        Relative paths are anchored to the backend/ root.  The resolved
        directory is rejected if it would sit inside the `app/` package,
        which keeps uploaded PHI out of importable source directories.
        """
        raw = Path(configured).expanduser()
        root = raw if raw.is_absolute() else (_backend_root() / raw)

        root = root.resolve()
        app_pkg = (_backend_root() / "app").resolve()
        if root == app_pkg or app_pkg in root.parents:
            raise StorageError(
                "DOCUMENT_STORAGE_PATH must not point inside the app package.",
                internal_detail=f"resolved storage root={root}",
            )
        return root

    @property
    def root(self) -> Path:
        """Absolute storage root directory."""
        return self._root

    def ensure_root(self) -> None:
        """Create the storage root if it does not yet exist."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                "Could not prepare document storage.",
                internal_detail=f"mkdir failed: {exc.__class__.__name__}",
            ) from exc

    def path_for(self, stored_filename: str) -> Path:
        """
        Return the absolute path for a stored filename, guaranteeing the
        result stays inside the storage root.

        This is the ONLY sanctioned way to turn a stored filename into a
        path, which makes traversal structurally impossible.
        """
        candidate = (self._root / stored_filename).resolve()
        if candidate.parent != self._root:
            raise StorageError(
                "Invalid stored document path.",
                internal_detail=f"rejected path={candidate}",
            )
        return candidate

    def exists(self, stored_filename: str) -> bool:
        try:
            return self.path_for(stored_filename).is_file()
        except StorageError:
            return False

    def delete(self, stored_filename: str) -> None:
        """Remove a stored file if present.  Failures are logged, not raised."""
        try:
            target = self.path_for(stored_filename)
            if target.is_file():
                target.unlink()
                logger.info("Deleted stored document: filename=%s", stored_filename)
        except (StorageError, OSError) as exc:
            logger.warning(
                "Could not delete stored document filename=%s error=%s",
                stored_filename,
                exc.__class__.__name__,
            )

    # ── Validation ───────────────────────────────────────────────────────────

    @staticmethod
    def sanitize_original_filename(filename: str | None) -> str:
        """
        Reduce a client filename to a safe DISPLAY name.

        Strips any directory component, neutralises traversal sequences,
        removes unsafe characters, and bounds the length.  The result is
        stored for display only and is never used as a path.
        """
        if not filename:
            return "document"

        # Drop directory components for both POSIX and Windows separators.
        base = filename.replace("\\", "/").split("/")[-1]
        # Remove control characters.
        base = "".join(ch for ch in base if ch.isprintable())
        # Neutralise traversal and leading dots.
        base = _MULTI_DOT.sub(".", base).lstrip(". ")
        base = _UNSAFE_NAME_CHARS.sub("_", base)
        base = base.strip("_ ")

        if not base:
            return "document"
        if len(base) > 200:
            stem, dot, ext = base.rpartition(".")
            if dot and len(ext) <= 8:
                base = stem[: 200 - len(ext) - 1] + "." + ext
            else:
                base = base[:200]
        return base

    @classmethod
    def extract_extension(cls, filename: str | None) -> str:
        """Return the lowercased extension of a filename, including the dot."""
        base = cls.sanitize_original_filename(filename)
        _, dot, ext = base.rpartition(".")
        return f".{ext.lower()}" if dot else ""

    def validate_upload(
        self,
        *,
        filename: str | None,
        content_type: str | None,
        data: bytes,
    ) -> tuple[str, str]:
        """
        Validate an upload and return ``(safe_display_name, extension)``.

        Checks, in order: presence of data, declared extension, declared
        content type, and finally the real magic bytes.  Declared metadata
        is never trusted on its own.
        """
        if not data:
            raise DocumentValidationError("The uploaded file is empty.")

        extension = self.extract_extension(filename)
        if extension not in ALLOWED_EXTENSIONS:
            raise UnsupportedFileTypeError()

        declared = (content_type or "").split(";")[0].strip().lower()
        if declared not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedFileTypeError()

        # The declared content type must agree with the extension.  JPEG has
        # two widely used spellings (image/jpeg and the older image/jpg), so
        # both are accepted for .jpg/.jpeg and never mixed with PDF or PNG.
        expected = EXTENSION_CONTENT_TYPES[extension]
        jpeg_spellings = {"image/jpeg", "image/jpg"}
        compatible = (
            {declared, expected} <= jpeg_spellings
            if extension in (".jpg", ".jpeg")
            else declared == expected
        )
        if not compatible:
            raise DocumentValidationError(
                "The file extension does not match the declared content type."
            )

        # Content sniffing: the bytes must actually be the claimed format.
        header = data[:8]
        matched = False
        for signature, extensions in _MAGIC_SIGNATURES:
            if header.startswith(signature) and extension in extensions:
                matched = True
                break
        if not matched:
            raise DocumentValidationError(
                "The file contents do not match a supported document format."
            )

        return self.sanitize_original_filename(filename), extension

    def enforce_size_limit(self, size: int) -> None:
        """Raise if the byte count exceeds MAX_UPLOAD_SIZE_MB."""
        limit = self._settings.max_upload_size_bytes
        if size > limit:
            raise FileTooLargeError(
                f"The uploaded file exceeds the maximum size of "
                f"{self._settings.max_upload_size_mb} MB."
            )

    # ── Writing ──────────────────────────────────────────────────────────────

    @staticmethod
    def build_stored_filename(extension: str) -> str:
        """
        Generate a server-side storage filename.

        Always a fresh UUID plus a validated extension.  The client
        filename never contributes to this value.
        """
        if extension not in ALLOWED_EXTENSIONS:
            raise DocumentValidationError("Invalid file extension.")
        return f"{uuid.uuid4().hex}{extension}"

    def store(
        self,
        *,
        data: bytes,
        filename: str | None,
        content_type: str | None,
    ) -> StoredDocument:
        """
        Validate and persist an upload, returning its metadata.

        The write goes to a temporary sibling file and is then moved into
        place, so a partial write is never visible under the final name.
        """
        safe_name, extension = self.validate_upload(
            filename=filename, content_type=content_type, data=data
        )
        self.enforce_size_limit(len(data))

        digest = hashlib.sha256(data).hexdigest()
        stored_filename = self.build_stored_filename(extension)
        self.ensure_root()
        target = self.path_for(stored_filename)

        tmp_target = target.with_name(f".{stored_filename}.part")
        try:
            with open(tmp_target, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_target, target)
        except OSError as exc:
            # Best-effort cleanup of the partial file.
            try:
                if tmp_target.exists():
                    tmp_target.unlink()
            except OSError:
                pass
            raise StorageError(
                "Failed to store the uploaded document.",
                internal_detail=f"write failed: {exc.__class__.__name__}",
            ) from exc

        logger.info(
            "Stored discharge document: filename=%s size=%s ext=%s",
            stored_filename,
            len(data),
            extension,
        )

        return StoredDocument(
            stored_filename=stored_filename,
            file_path=str(target),
            file_size=len(data),
            sha256_hash=digest,
            content_type=EXTENSION_CONTENT_TYPES[extension],
            original_filename=safe_name,
        )

    # ── Reading ──────────────────────────────────────────────────────────────

    def read(self, stored_filename: str) -> bytes:
        """Read a stored document's bytes.  Raises StorageError if missing."""
        target = self.path_for(stored_filename)
        if not target.is_file():
            raise StorageError(
                "The stored document file is missing.",
                internal_detail=f"not found: {target}",
            )
        try:
            return target.read_bytes()
        except OSError as exc:
            raise StorageError(
                "The stored document file could not be read.",
                internal_detail=f"read failed: {exc.__class__.__name__}",
            ) from exc


def normalize_for_hashing(value: str) -> str:  # pragma: no cover - helper
    """Normalise text for consistent hashing comparisons."""
    return unicodedata.normalize("NFKC", value).strip().lower()
