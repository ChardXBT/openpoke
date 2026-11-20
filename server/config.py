"""Simplified configuration management."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


def _load_env_file() -> None:
    """Load .env from root directory if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                key, value = key.strip(), value.strip().strip("'\"")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_env_file()


DEFAULT_APP_NAME = "OpenPoke Server"
DEFAULT_APP_VERSION = "0.3.0"


def _env_int(name: str, fallback: int) -> int:
    try:
        return int(os.getenv(name, str(fallback)))
    except (TypeError, ValueError):
        return fallback


DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_OPENAI_MODEL = "gpt-6.1-terra"
ROUTER_MODES = ("reference", "legacy")
PROVIDERS = ("openrouter", "openai")


def _env_provider() -> str:
    provider = (os.getenv("OPENPOKE_PROVIDER") or "openrouter").strip().lower()
    return provider if provider in PROVIDERS else "openrouter"


def _env_model() -> str:
    explicit = (os.getenv("OPENPOKE_MODEL") or "").strip()
    if explicit:
        return explicit
    return DEFAULT_OPENAI_MODEL if _env_provider() == "openai" else DEFAULT_MODEL


def _env_router_mode() -> str:
    mode = (os.getenv("OPENPOKE_ROUTER") or "reference").strip().lower()
    return mode if mode in ROUTER_MODES else "reference"


class Settings(BaseModel):
    """Application settings with lightweight env fallbacks."""

    # App metadata
    app_name: str = Field(default=DEFAULT_APP_NAME)
    app_version: str = Field(default=DEFAULT_APP_VERSION)

    # Server runtime
    # Localhost by default: the API has no auth and can read and send your email, so it must not
    # be reachable from the network unless you choose that (OPENPOKE_HOST=0.0.0.0).
    server_host: str = Field(default=os.getenv("OPENPOKE_HOST", "127.0.0.1"))
    server_port: int = Field(default=_env_int("OPENPOKE_PORT", 8001))

    # LLM model selection. anthropic/claude-sonnet-4 is no longer served on
    # OpenRouter (checked 2026-11-20); OPENPOKE_MODEL overrides all five.
    interaction_agent_model: str = Field(default_factory=lambda: _env_model())
    execution_agent_model: str = Field(default_factory=lambda: _env_model())
    execution_agent_search_model: str = Field(default_factory=lambda: _env_model())
    summarizer_model: str = Field(default_factory=lambda: _env_model())
    email_classifier_model: str = Field(default_factory=lambda: _env_model())

    # Agent routing: "reference" (bounded working set + resolver) or "legacy"
    # (upstream behaviour: every agent name on every turn).
    router_mode: str = Field(default_factory=lambda: _env_router_mode())
    # Execution agents replay at most this many past requests of their own history.
    execution_history_limit: int = Field(
        default_factory=lambda: _env_int("OPENPOKE_EXEC_HISTORY_LIMIT", 20)
    )

    # Credentials / integrations
    openrouter_api_key: Optional[str] = Field(default=os.getenv("OPENROUTER_API_KEY"))
    # LLM provider: "openrouter" (default) or "openai" (OpenAI API directly with OPENAI_API_KEY).
    llm_provider: str = Field(default_factory=lambda: _env_provider())
    openai_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    composio_gmail_auth_config_id: Optional[str] = Field(default=os.getenv("COMPOSIO_GMAIL_AUTH_CONFIG_ID"))
    composio_api_key: Optional[str] = Field(default=os.getenv("COMPOSIO_API_KEY"))

    # HTTP behaviour
    # Only the web UI's origin by default: with "*", any web page you visit could script your Poke.
    # (The web UI calls the API server-side, so it needs no CORS at all.)
    cors_allow_origins_raw: str = Field(
        default=os.getenv("OPENPOKE_CORS_ALLOW_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"))
    enable_docs: bool = Field(default=os.getenv("OPENPOKE_ENABLE_DOCS", "1") != "0")
    docs_url: Optional[str] = Field(default=os.getenv("OPENPOKE_DOCS_URL", "/docs"))

    # Summarisation controls
    conversation_summary_threshold: int = Field(default=100)
    conversation_summary_tail_size: int = Field(default=10)

    @property
    def cors_allow_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_allow_origins_raw.strip() in {"", "*"}:
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins_raw.split(",") if origin.strip()]

    @property
    def resolved_docs_url(self) -> Optional[str]:
        """Return documentation URL when docs are enabled."""
        return (self.docs_url or "/docs") if self.enable_docs else None

    # Personal memory: learn lasting facts from the user's own messages (cheap model, background).
    learn_enabled: bool = Field(default_factory=lambda: os.getenv("OPENPOKE_LEARN", "1") != "0")
    # Demo mailbox: a local fake Gmail (nothing is sent anywhere); OPENPOKE_FAKE_GMAIL=1.
    fake_gmail: bool = Field(default_factory=lambda: os.getenv("OPENPOKE_FAKE_GMAIL", "0") == "1")
    memory_model: str = Field(default_factory=lambda: (os.getenv("OPENPOKE_MEMORY_MODEL") or (
        "gpt-6-luna" if _env_provider() == "openai" else "anthropic/claude-haiku-5.5")).strip())

    @property
    def llm_api_key(self) -> Optional[str]:
        """API key for the active provider."""
        return self.openai_api_key if self.llm_provider == "openai" else self.openrouter_api_key

    @property
    def summarization_enabled(self) -> bool:
        """Flag indicating conversation summarisation is active."""
        return self.conversation_summary_threshold > 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
