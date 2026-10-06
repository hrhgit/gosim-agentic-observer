"""Environment-driven LangChain model construction for the minimal agent.

On the platform (cloud runs and the official local project runner) every run gets
OPENAI_BASE_URL and OPENAI_API_KEY: an OpenAI-compatible chat-completions proxy and a
temporary run credential. The proxy calls your team's provider from the Participate page
with the model name sent from here; "team-model" (or no model) means the team's default
model set on that page. These two variables therefore take
precedence for every OpenAI-compatible provider; the provider-specific variables
(MODEL_BASE_URL, ZAI_API_KEY, ...) remain the fallback for your own local runs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


PROVIDER_ALIASES = {
    "chatgpt": "openai",
    "claude": "anthropic",
    "grok": "xai",
    "glm": "zai",
    "kimi": "moonshot",
    "kimi_relay": "kimi-relay",
    "kimi-relay": "kimi-relay",
    "qwen": "dashscope",
}

PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "zai": "ZAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshot": "KIMI_API_KEY",
    "kimi-relay": "KIMI_RELAY_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


# Sent when the platform proxy is used without a model name; the proxy treats it as the
# team's default model set on the Participate page.
PLATFORM_MODEL_PLACEHOLDER = "team-model"
PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


class ModelConfigurationError(ValueError):
    """Raised when an optional model provider is only partially configured."""


def _normalise_profile_name(value: str) -> str:
    """Return a safe environment-variable suffix for a named local profile."""
    if not value:
        return ""
    if value.lower() == "deterministic":
        return "deterministic"
    if not PROFILE_NAME_PATTERN.fullmatch(value):
        raise ModelConfigurationError(
            "MODEL_PROFILE must be blank, deterministic, or use letters, digits, _ and -"
        )
    return value.lower().replace("-", "_")


def _profile_value(profile: str, setting: str) -> str | None:
    """Read one selected profile setting without falling back to another profile."""
    raw = os.environ.get(f"MODEL_PROFILE_{profile.upper()}_{setting}")
    return raw.strip() if raw is not None else None


@dataclass(frozen=True)
class ModelSettings:
    """Secret-safe provider settings loaded from process environment variables."""

    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    api_mode: str = "chat"
    timeout_seconds: float = 30.0
    max_retries: int = 1
    top_k_candidates: int = 12
    profile: str = ""

    @classmethod
    def from_environment(cls, dotenv_path: Path | None = None) -> "ModelSettings":
        try:
            from dotenv import load_dotenv
        except ModuleNotFoundError:
            load_dotenv = None
        if load_dotenv is not None:
            load_dotenv(dotenv_path=dotenv_path, override=False)
        timeout_seconds = float(os.environ.get("LLM_TIMEOUT_SECONDS", "30"))
        max_retries = int(os.environ.get("LLM_MAX_RETRIES", "1"))
        top_k_candidates = int(os.environ.get("LLM_TOP_K_CANDIDATES", "12"))
        selected_profile = os.environ.get("MODEL_PROFILE", "").strip()
        profile = _normalise_profile_name(selected_profile)
        if profile == "deterministic":
            return cls(
                provider="deterministic",
                model="",
                base_url="",
                api_key="",
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                top_k_candidates=top_k_candidates,
                profile=profile,
            )
        if profile:
            provider_value = _profile_value(profile, "PROVIDER")
            if not provider_value:
                profile_prefix = f"MODEL_PROFILE_{profile.upper()}"
                raise ModelConfigurationError(
                    f"MODEL_PROFILE={selected_profile!r} requires {profile_prefix}_PROVIDER"
                )
            provider = provider_value.lower()
            explicit_key_name = _profile_value(profile, "API_KEY_ENV") or ""
            local_base_url = _profile_value(profile, "BASE_URL") or ""
            local_model = _profile_value(profile, "NAME") or ""
            configured_api_mode = _profile_value(profile, "API_MODE")
        else:
            provider = os.environ.get("MODEL_PROVIDER", "deterministic").strip().lower()
            explicit_key_name = os.environ.get("MODEL_API_KEY_ENV", "").strip()
            local_base_url = os.environ.get("MODEL_BASE_URL", "").strip()
            local_model = os.environ.get("MODEL_NAME", "").strip()
            configured_api_mode = os.environ.get("MODEL_API_MODE")
        provider = PROVIDER_ALIASES.get(provider, provider)
        key_name = explicit_key_name or PROVIDER_KEY_ENV.get(provider, "MODEL_API_KEY")
        key_names = [key_name]
        # Keep the old variable working for existing Moonshot Platform setups.
        if provider == "moonshot" and not explicit_key_name:
            key_names.append("MOONSHOT_API_KEY")
        # OPENAI_BASE_URL/OPENAI_API_KEY first (what the platform injects), then the
        # provider-specific settings. Anthropic's native API is not OpenAI-compatible.
        compatible_base = (
            os.environ.get("OPENAI_BASE_URL", "").strip() if provider != "anthropic" else ""
        )
        base_url = compatible_base or local_base_url
        api_key = (os.environ.get("OPENAI_API_KEY", "").strip() if compatible_base else "")
        if not api_key:
            api_key = next(
                (
                    os.environ.get(name, "").strip()
                    for name in key_names
                    if os.environ.get(name, "").strip()
                ),
                "",
            )
        model = (
            os.environ.get("OPENAI_MODEL", "").strip()
            or local_model
            or (PLATFORM_MODEL_PLACEHOLDER if compatible_base else "")
        )
        # The platform proxy speaks chat completions only.
        api_mode = (
            "chat"
            if compatible_base
            else (
                configured_api_mode
                or ("responses" if provider == "openai" else "chat")
            ).lower()
        )
        return cls(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_mode=api_mode,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            top_k_candidates=top_k_candidates,
            profile=profile,
        )

    @property
    def deterministic(self) -> bool:
        return self.provider in {"", "none", "deterministic"}


def build_chat_model(settings: ModelSettings):
    """Build one optional LangChain chat model without leaking its credential."""
    if settings.deterministic:
        return None
    if not settings.model or not settings.api_key:
        raise ModelConfigurationError(
            f"{settings.provider} requires a model name (OPENAI_MODEL or MODEL_NAME) "
            "and an API key (OPENAI_API_KEY or its provider key)"
        )
    if settings.timeout_seconds <= 0 or settings.max_retries < 0:
        raise ModelConfigurationError("timeout must be positive and retries non-negative")
    if settings.top_k_candidates < 1:
        raise ModelConfigurationError("LLM_TOP_K_CANDIDATES must be positive")
    if settings.provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ModuleNotFoundError as exc:
            raise ModelConfigurationError(
                "install langchain-anthropic to use Anthropic"
            ) from exc
        return ChatAnthropic(
            model=settings.model,
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
    try:
        from langchain_openai import ChatOpenAI
    except ModuleNotFoundError as exc:
        raise ModelConfigurationError(
            "install langchain-openai to use OpenAI or an OpenAI-compatible endpoint"
        ) from exc
    if settings.provider != "openai" and not settings.base_url:
        raise ModelConfigurationError(
            f"{settings.provider} requires OPENAI_BASE_URL or MODEL_BASE_URL for its compatible endpoint"
        )
    kwargs = {
        "model": settings.model,
        "api_key": settings.api_key,
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if settings.provider == "openai":
        if settings.api_mode not in {"chat", "responses"}:
            raise ModelConfigurationError("MODEL_API_MODE must be chat or responses")
        kwargs["use_responses_api"] = settings.api_mode == "responses"
    else:
        kwargs["use_responses_api"] = False
    return ChatOpenAI(**kwargs)
