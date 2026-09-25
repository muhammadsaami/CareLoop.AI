"""
CareLoop AI — Phase 2: Secret Redaction Tests

The client-safe-message guarantee depends on redaction catching keys in any
format, including ones this process never configured.  Clinical prose must
pass through untouched.
"""
import pytest

from app.core.redaction import REDACTED, redact_secrets


class TestPatternRedaction:
    @pytest.mark.parametrize(
        "text",
        [
            "gsk_" + "A" * 48,
            "AIzaSy" + "B" * 35,
            "sk-" + "C" * 32,
            "ghp_" + "D" * 36,
            "xoxb-123456789012-abcdefghijkl",
        ],
    )
    def test_known_key_shapes_are_masked(self, text):
        out = redact_secrets(f"auth failed using {text}")
        assert REDACTED in out
        assert text not in out

    def test_bearer_token_masked(self):
        token = "Bearer " + "e" * 40
        out = redact_secrets(f"header was {token}")
        assert "e" * 40 not in out

    def test_key_embedded_in_prose(self):
        key = "gsk_" + "Z" * 44
        out = redact_secrets(f"provider said: your key {key} is invalid")
        assert key not in out
        assert "provider said" in out
        assert "is invalid" in out

    def test_multiple_keys_all_masked(self):
        a = "gsk_" + "A" * 48
        b = "AIza" + "B" * 40
        out = redact_secrets(f"{a} and {b}")
        assert a not in out
        assert b not in out


class TestExactRedaction:
    def test_configured_groq_key_masked(self, phase2_settings):
        key = "my-private-groq-key-0001"
        settings = phase2_settings.model_copy(update={"groq_api_key": key})
        out = redact_secrets(f"failed with {key}", settings=settings)
        assert key not in out
        assert REDACTED in out

    def test_configured_database_url_masked(self, phase2_settings):
        url = "postgresql+psycopg://user:hunter2pass@localhost:5432/careloop"
        settings = phase2_settings.model_copy(update={"database_url": url})
        out = redact_secrets(f"cannot connect to {url}", settings=settings)
        assert "hunter2pass" not in out

    def test_extra_secrets_argument(self):
        out = redact_secrets(
            "value was abcdef123456xyz", extra_secrets=["abcdef123456xyz"]
        )
        assert "abcdef123456xyz" not in out

    def test_longest_secret_replaced_first(self):
        """A short secret that prefixes a longer one must not shadow it."""
        short = "secret-token"
        long = "secret-token-extended-value"
        out = redact_secrets(long, extra_secrets=[short, long])
        assert long not in out

    def test_settings_without_attributes_is_tolerated(self):
        out = redact_secrets("normal message", settings=object())
        assert out == "normal message"


class TestClinicalProseIsUntouched:
    @pytest.mark.parametrize(
        "text",
        [
            "Metformin 500 mg twice daily with meals",
            "Take 1 tablet PO daily; do not crush",
            "Fever above 101 F: contact your care team",
            "Cardiology on 2026-10-15 at Main Clinic, Room 204",
            "The uploaded document could not be read.",
            "Patient Jane Doe, discharge date 2026-09-20",
        ],
    )
    def test_ordinary_messages_pass_through(self, text):
        assert redact_secrets(text) == text

    def test_empty_and_none(self):
        assert redact_secrets("") == ""
        assert redact_secrets(None) is None
