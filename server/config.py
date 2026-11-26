"""Simplified configuration management."""

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


def _env_file_path() -> Path:
    """Return the env file used for local single-owner configuration."""
    override = (os.getenv("OPENPOKE_ENV_FILE") or "").strip()
    return Path(override) if override else Path(__file__).parent.parent / ".env"


def _load_env_file() -> None:
    """Load .env from root directory if present."""
    env_path = _env_file_path()
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
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
ROUTER_MODES = ("reference", "legacy")
PROVIDERS = ("openrouter", "openai", "anthropic")


def _env_provider() -> str:
    provider = (os.getenv("OPENPOKE_PROVIDER") or "openrouter").strip().lower()
    return provider if provider in PROVIDERS else "openrouter"


def _env_model() -> str:
    explicit = (os.getenv("OPENPOKE_MODEL") or "").strip()
    if explicit:
        return explicit
    return {"openai": DEFAULT_OPENAI_MODEL, "anthropic": DEFAULT_ANTHROPIC_MODEL}.get(_env_provider(), DEFAULT_MODEL)


def _env_router_mode() -> str:
    mode = (os.getenv("OPENPOKE_ROUTER") or "reference").strip().lower()
    return mode if mode in ROUTER_MODES else "reference"


class Settings(BaseModel):
    """Application settings with lightweight env fallbacks."""

    # App metadata
    app_name: str = Field(default=DEFAULT_APP_NAME)
    app_version: str = Field(default=DEFAULT_APP_VERSION)

    # Server runtime
    # Localhost by default. API routes require the owner token, but localhost binding is
    # still the safe default; choose OPENPOKE_HOST=0.0.0.0 only behind a private tunnel.
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
    # OPENPOKE_PROVIDER=anthropic: Claude directly through the Anthropic SDK with ANTHROPIC_API_KEY.
    anthropic_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    # Needed when the key isn't scoped to a workspace (the API then asks for anthropic-workspace-id).
    anthropic_workspace_id: Optional[str] = Field(default_factory=lambda: (os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip() or None)
    composio_gmail_auth_config_id: Optional[str] = Field(default=os.getenv("COMPOSIO_GMAIL_AUTH_CONFIG_ID"))
    composio_api_key: Optional[str] = Field(default=os.getenv("COMPOSIO_API_KEY"))
    # Single-owner API token. Generated on first server run and stored in .env; never sent to browsers.
    owner_token: Optional[str] = Field(default_factory=lambda: (os.getenv("OPENPOKE_OWNER_TOKEN") or "").strip() or None)

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
        {"openai": "gpt-6-luna", "anthropic": "claude-haiku-5-5"}.get(_env_provider(), "anthropic/claude-haiku-5.5"))).strip())

    @property
    def llm_api_key(self) -> Optional[str]:
        """API key for the active provider."""
        return {"openai": self.openai_api_key, "anthropic": self.anthropic_api_key}.get(
            self.llm_provider, self.openrouter_api_key)

    @property
    def summarization_enabled(self) -> bool:
        """Flag indicating conversation summarisation is active."""
        return self.conversation_summary_threshold > 0


def ensure_owner_token(settings: Settings) -> str:
    """Return the owner token, generating and persisting a strong one on first run.

    The API is fail-closed: callers never get a token-less escape hatch, and startup raises
    if the generated token cannot be persisted for the server-side web proxy to use.
    """
    configured = (settings.owner_token or os.getenv("OPENPOKE_OWNER_TOKEN") or "").strip()
    if configured:
        settings.owner_token = configured
        os.environ["OPENPOKE_OWNER_TOKEN"] = configured
        return configured

    env_path = _env_file_path()
    lines: list[str] = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeError("OPENPOKE_OWNER_TOKEN is missing and .env cannot be read") from exc

    token_line = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != "OPENPOKE_OWNER_TOKEN":
            continue
        value = value.strip().strip("'\"")
        if value:
            settings.owner_token = value
            os.environ["OPENPOKE_OWNER_TOKEN"] = value
            return value
        if token_line < 0:
            token_line = index

    token = secrets.token_urlsafe(32)
    new_line = f"OPENPOKE_OWNER_TOKEN={token}"
    if token_line >= 0:
        lines[token_line] = new_line
    else:
        lines.append(new_line)

    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = env_path.with_name(f".{env_path.name}.{os.getpid()}.tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(env_path)
    except OSError as exc:
        raise RuntimeError("OPENPOKE_OWNER_TOKEN is missing and could not be written to .env") from exc

    settings.owner_token = token
    os.environ["OPENPOKE_OWNER_TOKEN"] = token
    return token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
