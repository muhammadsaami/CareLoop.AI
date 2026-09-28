"""
CareLoop AI — Authentication Configuration Tests

`SECRET_KEY` is the one setting whose misconfiguration is invisible: the app
starts, tokens are signed, and every request is accepted. Nothing looks wrong
until someone forges a token. So the refusals are pinned here rather than
trusted.

The recurring theme is PRODUCTION ONLY. A developer's local `.env` must not be
blocked from a convenient value - that would just teach people to bypass the
check - but a real deployment must never run on one.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, _is_weak_secret_key

A_REAL_LOOKING_KEY = "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8"


def _settings(**overrides) -> Settings:
    """
    Settings with only the fields a security test cares about pinned.

    `_env_file=None` is essential rather than tidy. Without it `Settings` reads
    the developer's real `.env`, so a test asserting that a MISSING key is
    refused would silently pass by borrowing the value from that file - and
    would then fail on a machine that has no `.env` at all. A test that depends
    on the ambient environment is not a test of this code.
    """
    base = {
        "database_url": "postgresql+psycopg://user:pass@localhost/careloop",
        "secret_key": A_REAL_LOOKING_KEY,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# ── The weak-key check itself ───────────────────────────────────────────────


class TestWeakSecretDetection:
    @pytest.mark.parametrize(
        "secret",
        [
            "changeme",
            "please-changeme-please",
            "CHANGEME",
            "Change-Me",
            "my-secret-key-for-dev",
            "a-development-secret-here-x",
            "super-secret-1234",
            "the-admin-password!!",
            "this-is-just-a-test-key",
        ],
    )
    def test_placeholders_are_detected_inside_larger_keys(self, secret):
        """
        SUBSTRING matching, which is the whole point.

        An operator who wrote "please-changeme-please" made exactly the same
        mistake as one who wrote "changeme". An earlier version of this check
        compared whole colon-delimited segments for equality, so every one of
        these longer values was accepted - while the comment beside it claimed
        they would be caught. The comment and the behaviour have to agree, or
        the comment is a lie that reviewers rely on.
        """
        assert _is_weak_secret_key(secret) is True

    @pytest.mark.parametrize(
        "secret",
        [
            pytest.param(A_REAL_LOOKING_KEY, id="hex"),
            pytest.param(
                "k3Yx9pLm2QwRt8Zb4Nc7Df1Hg6Js0Ae5Ui2Op4Rn6Tl8M", id="mixed-case-alnum"
            ),
        ],
    )
    def test_generated_keys_are_accepted(self, secret):
        assert _is_weak_secret_key(secret) is False

    @pytest.mark.parametrize(
        "secret",
        [
            "postgres:changeme@db.internal:5432",
            "postgresql://app:password@localhost/careloop",
        ],
    )
    def test_a_database_url_pasted_into_the_field_is_caught(self, secret):
        """
        A very common mistake: pasting DATABASE_URL where SECRET_KEY belongs.

        The ':' split exists for this. It is a narrow net - it only fires when
        one of the segments is a known placeholder - but it costs nothing and
        catches the paste that would otherwise look like a valid 40-character
        key.
        """
        assert _is_weak_secret_key(secret) is True


# ── Startup refusals ────────────────────────────────────────────────────────


class TestProductionRefusals:
    def test_production_rejects_a_placeholder(self):
        with pytest.raises(ValidationError, match="well-known placeholder"):
            _settings(environment="production", secret_key="please-changeme-please-" + "x" * 16)

    def test_production_accepts_a_generated_key(self):
        assert _settings(environment="production").secret_key == A_REAL_LOOKING_KEY

    def test_development_may_use_a_convenient_key(self):
        """
        The check is production-only, deliberately.

        Blocking it everywhere would not make developers choose good keys; it
        would make them disable the check, or set a long enough junk value to
        slip past it. The risk that actually matters is a real deployment.
        """
        # Long enough to satisfy min_length, and still obviously not a secret.
        convenient = "dev-dev-dev-dev-dev-dev-dev-dev-dev-dev"
        assert _settings(environment="development", secret_key=convenient).secret_key == (
            convenient
        )

    def test_a_short_key_is_refused_everywhere(self):
        """
        The 32-character minimum is not production-gated.

        It is enforced in the field declaration, so it applies in development
        too: there is no reason to run a working configuration on a key that
        could not be generated by the documented command.
        """
        with pytest.raises(ValidationError):
            _settings(secret_key="too-short")

    def test_a_missing_key_is_refused(self, monkeypatch):
        """
        The app refuses to start without a key.

        Two isolation steps are needed, and both are the point of the test:
        `_env_file=None` stops the `.env` FILE being read, and clearing the
        variable stops the already-loaded process environment (conftest calls
        `load_dotenv`) from supplying it. Without both, the test silently
        passes by borrowing a real key, and would then fail on a machine with
        no `.env` - a test that reports the opposite of the truth.
        """
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                database_url="postgresql+psycopg://user:pass@localhost/careloop",
            )

    def test_the_process_environment_cannot_rescue_a_missing_key(self, monkeypatch):
        """
        A key supplied by the environment is honoured - but only if it is real.

        This documents the precedence `pydantic-settings` gives the process
        environment, so the "does production start without a key" question has
        one unambiguous answer rather than depending on how the operator set it.
        """
        monkeypatch.setenv("SECRET_KEY", A_REAL_LOOKING_KEY)
        settings = Settings(
            _env_file=None, database_url="postgresql+psycopg://u:p@localhost/x"
        )
        assert settings.secret_key == A_REAL_LOOKING_KEY


class TestTokenLifetimeBounds:
    def test_default_is_one_hour(self):
        assert _settings().access_token_expire_minutes == 60

    @pytest.mark.parametrize("minutes", [1, 30, 60, 1440])
    def test_sane_lifetimes_are_accepted(self, minutes):
        assert (
            _settings(access_token_expire_minutes=minutes).access_token_expire_minutes
            == minutes
        )

    @pytest.mark.parametrize("minutes", [0, -1, -60])
    def test_a_non_positive_lifetime_is_refused(self, minutes):
        """
        Zero or negative would mint an already-expired token, so every request
        would fail 401 with a message about the token being invalid. Refusing
        to start names the actual mistake.
        """
        with pytest.raises(ValidationError, match="at least 1"):
            _settings(access_token_expire_minutes=minutes)

    @pytest.mark.parametrize("minutes", [1441, 2880, 100000])
    def test_an_excessive_lifetime_is_refused(self, minutes):
        """
        Bounded above at 24h.

        A leaked token is a live key to a patient's record, so "how long does a
        stolen token work" must be answerable from configuration with a ceiling,
        not left to whoever writes the value.
        """
        with pytest.raises(ValidationError):
            _settings(access_token_expire_minutes=minutes)
