"""
CareLoop AI — LLM Provider Abstraction (Phase 2)

Provider-agnostic structured extraction.

`ExtractionService` depends only on `LLMProvider`; no vendor SDK is
imported here.  Providers are created by `factory.get_provider()`.
"""
from app.llm.base import LLMProvider, parse_json_payload
from app.llm.factory import PROVIDERS, SUPPORTED_PROVIDERS, get_provider

__all__ = [
    "LLMProvider",
    "parse_json_payload",
    "get_provider",
    "PROVIDERS",
    "SUPPORTED_PROVIDERS",
]
