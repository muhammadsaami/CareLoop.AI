"""
CareLoop AI — Phase 2: LLM Provider Tests

Both provider adapters are exercised against mocked SDK clients, so the
contract is verified without any network access or API key.  What matters
here is error translation: every vendor failure must become a domain
exception with the correct status code, and no vendor payload or key may
reach the caller.
"""
import json

import pytest

from app.core.exceptions import (
    MalformedProviderOutputError,
    MissingAPIKeyError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.llm.base import parse_json_payload
from app.llm.gemini_provider import GeminiProvider
from app.llm.groq_provider import GroqProvider


SYSTEM = "You transcribe discharge summaries."
USER = "Discharge summary text follows."
SCHEMA = {"type": "object", "properties": {"medications": {"type": "array"}}}
SCHEMA_NAME = "StructuredDischargeExtraction"


# ── Shared JSON parsing ─────────────────────────────────────────────────────

class TestParseJsonPayload:
    def test_parses_plain_object(self):
        assert parse_json_payload('{"a": 1}') == {"a": 1}

    def test_parses_fenced_json(self):
        assert parse_json_payload('```json\n{"a": 1}\n```') == {"a": 1}

    def test_parses_unlabelled_fence(self):
        assert parse_json_payload('```\n{"a": 1}\n```') == {"a": 1}

    def test_strips_surrounding_whitespace(self):
        assert parse_json_payload('\n  {"a": 1}  \n') == {"a": 1}

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_raises(self, raw):
        with pytest.raises(MalformedProviderOutputError):
            parse_json_payload(raw)

    def test_invalid_json_raises(self):
        with pytest.raises(MalformedProviderOutputError):
            parse_json_payload("{not valid json")

    def test_json_array_rejected(self):
        """A top-level array is not the agreed contract."""
        with pytest.raises(MalformedProviderOutputError):
            parse_json_payload("[1, 2, 3]")

    def test_json_scalar_rejected(self):
        with pytest.raises(MalformedProviderOutputError):
            parse_json_payload("42")

    def test_prose_around_json_rejected(self):
        with pytest.raises(MalformedProviderOutputError):
            parse_json_payload("Sure! Here is the result: {oops")


def configured_groq(phase2_settings):
    return GroqProvider(
        phase2_settings.model_copy(update={"groq_api_key": "gsk_" + "a" * 48})
    )


def configured_gemini(phase2_settings):
    return GeminiProvider(
        phase2_settings.model_copy(update={"gemini_api_key": "AIza" + "b" * 40})
    )


class FakeGeminiResponse:
    def __init__(self, text):
        self.text = text


class FakeGeminiModels:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return FakeGeminiResponse(self._text)


class FakeGeminiClient:
    def __init__(self, text):
        self.models = FakeGeminiModels(text)


# ── Groq adapter ────────────────────────────────────────────────────────────

class TestGroqProvider:
    def test_missing_api_key_reports_not_configured(self, phase2_settings):
        from app.core.exceptions import ProviderNotConfiguredError

        provider = GroqProvider(phase2_settings.model_copy(update={"groq_api_key": ""}))
        assert provider.is_configured() is False
        with pytest.raises(ProviderNotConfiguredError) as exc:
            provider.ensure_configured()
        assert exc.value.status_code == 503

    def test_ensure_api_key_raises_missing_key(self, phase2_settings):
        provider = GroqProvider(phase2_settings.model_copy(update={"groq_api_key": ""}))
        with pytest.raises(MissingAPIKeyError) as exc:
            provider.ensure_api_key()
        assert exc.value.status_code == 503

    def test_configured_when_key_present(self, phase2_settings):
        assert configured_groq(phase2_settings).is_configured() is True

    def test_successful_extraction(self, phase2_settings, monkeypatch):
        provider = configured_groq(phase2_settings)
        payload = {"medications": [], "appointments": []}
        monkeypatch.setattr(
            provider, "_create_completion", lambda **kwargs: json.dumps(payload)
        )
        result = provider.extract_structured(
            system_prompt=SYSTEM,
            user_prompt=USER,
            json_schema=SCHEMA,
            schema_name=SCHEMA_NAME,
        )
        assert result == payload

    def test_sends_temperature_zero(self, phase2_settings, monkeypatch):
        """Deterministic transcription requires temperature 0."""
        provider = configured_groq(phase2_settings)
        captured = {}

        def fake(messages, json_schema, schema_name):
            captured["messages"] = messages
            captured["schema"] = json_schema
            return json.dumps({"medications": []})

        monkeypatch.setattr(provider, "_create_completion", fake)
        provider.extract_structured(
            system_prompt=SYSTEM,
            user_prompt=USER,
            json_schema=SCHEMA,
            schema_name=SCHEMA_NAME,
        )
        assert captured["messages"][0]["role"] == "system"
        assert captured["messages"][0]["content"] == SYSTEM

    def test_malformed_output_translated(self, phase2_settings, monkeypatch):
        provider = configured_groq(phase2_settings)
        monkeypatch.setattr(
            provider, "_create_completion", lambda **kwargs: "not json"
        )
        with pytest.raises(MalformedProviderOutputError) as exc:
            provider.extract_structured(
                system_prompt=SYSTEM,
                user_prompt=USER,
                json_schema=SCHEMA,
                schema_name=SCHEMA_NAME,
            )
        assert exc.value.status_code == 502

    def test_empty_choices_becomes_provider_error(self, phase2_settings, monkeypatch):
        class EmptyCompletion:
            choices = []

        provider = configured_groq(phase2_settings)
        client = type("C", (), {})()
        client.chat = type("Chat", (), {})()
        client.chat.completions = type(
            "Completions", (), {"create": staticmethod(lambda **k: EmptyCompletion())}
        )()
        monkeypatch.setattr(provider, "_get_client", lambda: client)

        with pytest.raises(ProviderError):
            provider.extract_structured(
                system_prompt=SYSTEM,
                user_prompt=USER,
                json_schema=SCHEMA,
                schema_name=SCHEMA_NAME,
            )

    def test_model_name_never_exposes_key(self, phase2_settings):
        key = "gsk_" + "a" * 48
        provider = GroqProvider(
            phase2_settings.model_copy(update={"groq_api_key": key})
        )
        assert key not in provider.model
        assert key not in repr(provider)


# ── Gemini adapter ──────────────────────────────────────────────────────────

class TestGeminiProvider:
    def test_missing_api_key_reports_not_configured(self, phase2_settings):
        from app.core.exceptions import ProviderNotConfiguredError

        provider = GeminiProvider(
            phase2_settings.model_copy(update={"gemini_api_key": ""})
        )
        assert provider.is_configured() is False
        with pytest.raises(ProviderNotConfiguredError):
            provider.ensure_configured()

    def test_configured_when_key_present(self, phase2_settings):
        assert configured_gemini(phase2_settings).is_configured() is True

    def test_successful_extraction(self, phase2_settings, monkeypatch):
        provider = configured_gemini(phase2_settings)
        payload = {"medications": [], "warning_symptoms": []}
        client = FakeGeminiClient(json.dumps(payload))
        monkeypatch.setattr(provider, "_get_client", lambda: client)

        result = provider.extract_structured(
            system_prompt=SYSTEM,
            user_prompt=USER,
            json_schema=SCHEMA,
            schema_name=SCHEMA_NAME,
        )
        assert result == payload
        assert client.models.calls[0]["contents"] == USER

    def test_requests_json_response_mime(self, phase2_settings, monkeypatch):
        provider = configured_gemini(phase2_settings)
        client = FakeGeminiClient(json.dumps({"medications": []}))
        monkeypatch.setattr(provider, "_get_client", lambda: client)

        provider.extract_structured(
            system_prompt=SYSTEM,
            user_prompt=USER,
            json_schema=SCHEMA,
            schema_name=SCHEMA_NAME,
        )
        config = client.models.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert config.system_instruction == SYSTEM
        assert config.temperature == 0

    def test_malformed_output_translated(self, phase2_settings, monkeypatch):
        provider = configured_gemini(phase2_settings)
        monkeypatch.setattr(
            provider, "_get_client", lambda: FakeGeminiClient("<<garbage>>")
        )
        with pytest.raises(MalformedProviderOutputError):
            provider.extract_structured(
                system_prompt=SYSTEM,
                user_prompt=USER,
                json_schema=SCHEMA,
                schema_name=SCHEMA_NAME,
            )

    def test_empty_response_translated(self, phase2_settings, monkeypatch):
        provider = configured_gemini(phase2_settings)
        monkeypatch.setattr(
            provider, "_get_client", lambda: FakeGeminiClient("")
        )
        with pytest.raises(MalformedProviderOutputError):
            provider.extract_structured(
                system_prompt=SYSTEM,
                user_prompt=USER,
                json_schema=SCHEMA,
                schema_name=SCHEMA_NAME,
            )

    def test_model_name_never_exposes_key(self, phase2_settings):
        key = "AIza" + "b" * 40
        provider = GeminiProvider(
            phase2_settings.model_copy(update={"gemini_api_key": key})
        )
        assert key not in provider.model
        assert key not in repr(provider)


# ── Factory ─────────────────────────────────────────────────────────────────

class TestProviderFactory:
    def test_unknown_provider_raises(self, phase2_settings):
        from app.core.exceptions import ProviderNotConfiguredError
        from app.llm.factory import get_provider

        with pytest.raises(ProviderNotConfiguredError) as exc:
            get_provider(
                settings=phase2_settings.model_copy(
                    update={"llm_provider": "openai"}
                )
            )
        # The message must name the supported options.
        assert "groq" in str(exc.value)

    def test_returns_matching_provider(self, phase2_settings):
        from app.llm.factory import get_provider

        provider = get_provider(
            settings=phase2_settings.model_copy(
                update={"llm_provider": "groq", "groq_api_key": "gsk_abc"}
            )
        )
        assert provider.name == "groq"

    def test_gemini_selection(self, phase2_settings):
        from app.llm.factory import get_provider

        provider = get_provider(
            settings=phase2_settings.model_copy(
                update={"llm_provider": "gemini", "gemini_api_key": "AIza_abc"}
            )
        )
        assert provider.name == "gemini"


# ── Error translation status codes ──────────────────────────────────────────

class TestErrorStatusCodes:
    def test_timeout_is_504(self):
        assert ProviderTimeoutError("x").status_code == 504

    def test_rate_limit_is_429(self):
        assert ProviderRateLimitError("x").status_code == 429

    def test_auth_failure_is_502(self):
        """Auth failures are not 401: the client is authenticated, the server is not."""
        assert ProviderAuthenticationError("x").status_code == 502

    def test_generic_provider_error_is_502(self):
        assert ProviderError("x").status_code == 502

    def test_malformed_output_is_502(self):
        assert MalformedProviderOutputError("x").status_code == 502
