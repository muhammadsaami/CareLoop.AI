"""
CareLoop AI — Phase 2: Document Storage Tests

Covers validation, filename sanitisation, path traversal rejection, atomic
writes, and safe read/delete.  No database, network, or OCR involvement.
"""
import io
import os

import pytest

from app.core.exceptions import (
    DocumentValidationError,
    FileTooLargeError,
    StorageError,
    UnsupportedFileTypeError,
)
from app.services.storage import DocumentStorageService


@pytest.fixture
def storage(phase2_settings):
    return DocumentStorageService(phase2_settings)


def png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="PNG")
    return buf.getvalue()


def jpg_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="JPEG")
    return buf.getvalue()


def tiny_pdf():
    """A minimal but structurally valid PDF."""
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


# ── Validation: extensions and declared types ───────────────────────────────

class TestValidation:
    def test_rejects_unsupported_extension(self, storage):
        with pytest.raises(UnsupportedFileTypeError):
            storage.validate_upload(
                filename="notes.txt", content_type="text/plain", data=b"hello"
            )

    def test_rejects_executable_extension(self, storage):
        with pytest.raises(UnsupportedFileTypeError):
            storage.validate_upload(
                filename="evil.exe", content_type="application/octet-stream", data=b"MZ"
            )

    def test_rejects_no_extension(self, storage):
        with pytest.raises(UnsupportedFileTypeError):
            storage.validate_upload(
                filename="discharge", content_type="application/pdf", data=tiny_pdf()
            )

    def test_rejects_disallowed_content_type(self, storage):
        with pytest.raises(UnsupportedFileTypeError):
            storage.validate_upload(
                filename="summary.pdf", content_type="text/html", data=tiny_pdf()
            )

    def test_accepts_pdf(self, storage):
        safe, ext = storage.validate_upload(
            filename="summary.pdf", content_type="application/pdf", data=tiny_pdf()
        )
        assert ext == ".pdf"
        assert safe == "summary.pdf"

    def test_accepts_png(self, storage):
        _safe, ext = storage.validate_upload(
            filename="scan.png", content_type="image/png", data=png_bytes()
        )
        assert ext == ".png"

    def test_accepts_jpeg_with_standard_type(self, storage):
        _safe, ext = storage.validate_upload(
            filename="scan.jpg", content_type="image/jpeg", data=jpg_bytes()
        )
        assert ext == ".jpg"

    def test_accepts_legacy_image_jpg_spelling(self, storage):
        """image/jpg is non-standard but still emitted by some clients."""
        _safe, ext = storage.validate_upload(
            filename="scan.jpg", content_type="image/jpg", data=jpg_bytes()
        )
        assert ext == ".jpg"

    def test_accepts_content_type_with_charset_parameter(self, storage):
        _safe, ext = storage.validate_upload(
            filename="scan.png",
            content_type="image/png; charset=binary",
            data=png_bytes(),
        )
        assert ext == ".png"

    def test_rejects_empty_file(self, storage):
        with pytest.raises(DocumentValidationError):
            storage.validate_upload(
                filename="summary.pdf", content_type="application/pdf", data=b""
            )

    def test_rejects_content_type_extension_mismatch(self, storage):
        """A PNG declared as a PDF must not be accepted."""
        with pytest.raises(DocumentValidationError):
            storage.validate_upload(
                filename="summary.pdf",
                content_type="application/pdf",
                data=png_bytes(),
            )

    def test_rejects_lying_extension(self, storage):
        """Valid PDF bytes, but the extension claims PNG."""
        with pytest.raises(DocumentValidationError):
            storage.validate_upload(
                filename="scan.png", content_type="image/png", data=tiny_pdf()
            )

    def test_rejects_renamed_executable(self, storage):
        """A polyglot/exe renamed to .pdf must still be rejected."""
        with pytest.raises(DocumentValidationError):
            storage.validate_upload(
                filename="invoice.pdf",
                content_type="application/pdf",
                data=b"MZ\x90\x00payload",
            )

    def test_uppercase_extension_is_accepted(self, storage):
        _safe, ext = storage.validate_upload(
            filename="SUMMARY.PDF", content_type="application/pdf", data=tiny_pdf()
        )
        assert ext == ".pdf"


# ── Size limit ──────────────────────────────────────────────────────────────

class TestSizeLimit:
    def test_rejects_oversized_file(self, storage):
        with pytest.raises(FileTooLargeError):
            storage.enforce_size_limit(storage._settings.max_upload_size_bytes + 1)

    def test_allows_file_at_limit(self, storage):
        assert (
            storage.enforce_size_limit(storage._settings.max_upload_size_bytes) is None
        )

    def test_store_rejects_oversized_upload(self, phase2_settings):
        tiny = DocumentStorageService(
            phase2_settings.model_copy(update={"max_upload_size_mb": 0})
        )
        with pytest.raises(FileTooLargeError):
            tiny.store(
                data=tiny_pdf(), filename="summary.pdf", content_type="application/pdf"
            )


# ── Filename sanitisation ───────────────────────────────────────────────────

class TestSanitisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "../../etc/passwd.pdf",
            "..\\..\\windows\\system32\\config.pdf",
            "/absolute/path/evil.pdf",
            "C:/windows/evil.pdf",
        ],
    )
    def test_directory_components_are_stripped(self, storage, raw):
        safe = storage.sanitize_original_filename(raw)
        assert "/" not in safe
        assert "\\" not in safe
        assert ".." not in safe

    def test_traversal_filename_cannot_escape_root(self, storage):
        """End-to-end: a traversal name still lands directly in the root."""
        stored = storage.store(
            data=tiny_pdf(),
            filename="../../../etc/passwd.pdf",
            content_type="application/pdf",
        )
        written = storage.path_for(stored.stored_filename)
        assert written.parent == storage.root
        assert written.read_bytes().startswith(b"%PDF-")

    def test_control_characters_removed(self, storage):
        safe = storage.sanitize_original_filename("bad\x00name\x1b[31m.pdf")
        assert "\x00" not in safe
        assert "\x1b" not in safe

    def test_length_is_bounded(self, storage):
        safe = storage.sanitize_original_filename("a" * 500 + ".pdf")
        assert len(safe) <= 200
        assert safe.endswith(".pdf")

    def test_empty_name_falls_back(self, storage):
        assert storage.sanitize_original_filename("") == "document"
        assert storage.sanitize_original_filename(None) == "document"
        assert storage.sanitize_original_filename("///") == "document"

    def test_unicode_filename_preserved_safely(self, storage):
        safe = storage.sanitize_original_filename("RésuméΩ.pdf")
        assert safe.endswith(".pdf")
        assert "/" not in safe


# ── Stored filenames ────────────────────────────────────────────────────────

class TestStoredFilenames:
    def test_name_is_uuid_hex_plus_extension(self, storage):
        name = storage.build_stored_filename(".pdf")
        stem, ext = os.path.splitext(name)
        assert ext == ".pdf"
        assert len(stem) == 32
        int(stem, 16)  # valid hex

    def test_name_ignores_client_filename(self, storage):
        stored = storage.store(
            data=tiny_pdf(),
            filename="secret patient jane doe.pdf",
            content_type="application/pdf",
        )
        assert "jane" not in stored.stored_filename.lower()
        assert "secret" not in stored.stored_filename.lower()

    def test_same_name_twice_does_not_collide(self, storage):
        a = storage.build_stored_filename(".pdf")
        b = storage.build_stored_filename(".pdf")
        assert a != b

    def test_invalid_extension_rejected(self, storage):
        with pytest.raises(DocumentValidationError):
            storage.build_stored_filename(".exe")

    def test_duplicate_uploads_get_distinct_paths(self, storage):
        payload = tiny_pdf()
        first = storage.store(
            data=payload, filename="summary.pdf", content_type="application/pdf"
        )
        second = storage.store(
            data=payload, filename="summary.pdf", content_type="application/pdf"
        )
        assert first.stored_filename != second.stored_filename
        assert storage.path_for(first.stored_filename).read_bytes() == payload
        assert storage.path_for(second.stored_filename).read_bytes() == payload


# ── Read / exists / delete ──────────────────────────────────────────────────

class TestReadExistsDelete:
    def test_roundtrip(self, storage):
        payload = tiny_pdf()
        stored = storage.store(
            data=payload, filename="summary.pdf", content_type="application/pdf"
        )
        assert storage.exists(stored.stored_filename)
        assert storage.read(stored.stored_filename) == payload

        storage.delete(stored.stored_filename)
        assert not storage.exists(stored.stored_filename)

    def test_read_missing_raises_storage_error(self, storage):
        with pytest.raises(StorageError):
            storage.read("0" * 32 + ".pdf")

    def test_delete_is_idempotent(self, storage):
        stored = storage.store(
            data=tiny_pdf(), filename="summary.pdf", content_type="application/pdf"
        )
        storage.delete(stored.stored_filename)
        storage.delete(stored.stored_filename)  # must not raise

    def test_exists_returns_false_for_traversal_name(self, storage):
        assert storage.exists("../../../etc/passwd") is False

    def test_path_for_rejects_traversal(self, storage):
        with pytest.raises(StorageError):
            storage.path_for("../escaped.pdf")

    def test_path_for_accepts_nested_relative_escape(self, storage):
        with pytest.raises(StorageError):
            storage.path_for("sub/dir/file.pdf")


# ── Atomic write ────────────────────────────────────────────────────────────

class TestAtomicWrite:
    def test_no_partial_file_left_after_failure(self, storage, monkeypatch):
        """A write failure must not leave a visible partial document."""
        real_open = open

        def failing_open(path, *args, **kwargs):
            if str(path).endswith(".part"):
                raise OSError("disk full")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", failing_open)

        with pytest.raises(StorageError):
            storage.store(
                data=tiny_pdf(),
                filename="summary.pdf",
                content_type="application/pdf",
            )

        # No stray documents or .part files remain.
        assert list(storage.root.iterdir()) == []

    def test_successful_write_leaves_no_temp_file(self, storage):
        storage.store(
            data=tiny_pdf(), filename="summary.pdf", content_type="application/pdf"
        )
        assert not any(p.name.endswith(".part") for p in storage.root.iterdir())


# ── Storage root safety ─────────────────────────────────────────────────────

class TestStorageRootSafety:
    def test_relative_path_is_anchored_to_backend_root(self):
        svc = DocumentStorageService(
            type(
                "S",
                (),
                {"document_storage_path": "storage/unit-test-root"},
            )()
        )
        assert svc.root.is_absolute()
        assert svc.root.name == "unit-test-root"

    def test_storage_inside_app_package_is_rejected(self):
        with pytest.raises(StorageError):
            DocumentStorageService(
                type("S", (), {"document_storage_path": "app/uploads"})()
            )

    def test_ensure_root_creates_directory(self, phase2_settings, tmp_path):
        target = tmp_path / "brand-new-dir"
        svc = DocumentStorageService(
            phase2_settings.model_copy(update={"document_storage_path": str(target)})
        )
        assert not target.exists()
        svc.ensure_root()
        assert target.is_dir()
