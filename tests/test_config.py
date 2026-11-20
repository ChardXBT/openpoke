from server.config import Settings


def test_router_mode_defaults_and_env(monkeypatch):
    monkeypatch.delenv("OPENPOKE_ROUTER", raising=False)
    monkeypatch.delenv("OPENPOKE_EXEC_HISTORY_LIMIT", raising=False)
    settings = Settings()
    assert settings.router_mode == "reference"
    assert settings.execution_history_limit == 20

    monkeypatch.setenv("OPENPOKE_ROUTER", "legacy")
    monkeypatch.setenv("OPENPOKE_EXEC_HISTORY_LIMIT", "7")
    settings = Settings()
    assert settings.router_mode == "legacy"
    assert settings.execution_history_limit == 7


def test_unknown_router_mode_falls_back_to_reference(monkeypatch):
    monkeypatch.setenv("OPENPOKE_ROUTER", "nonsense")
    assert Settings().router_mode == "reference"


def test_default_model_is_served_successor(monkeypatch):
    monkeypatch.delenv("OPENPOKE_MODEL", raising=False)
    # anthropic/claude-sonnet-4 is no longer served on OpenRouter (checked 2026-11-20)
    assert Settings().interaction_agent_model == "anthropic/claude-sonnet-5.5"
    monkeypatch.setenv("OPENPOKE_MODEL", "anthropic/claude-haiku-5.5")
    assert Settings().interaction_agent_model == "anthropic/claude-haiku-5.5"


def test_server_is_local_only_by_default(monkeypatch):
    """The API has no auth and can send email: not on the network, not scriptable by other sites."""
    import importlib

    import server.config as config

    monkeypatch.delenv("OPENPOKE_HOST", raising=False)
    monkeypatch.delenv("OPENPOKE_CORS_ALLOW_ORIGINS", raising=False)
    importlib.reload(config)  # these defaults are read when the class is defined
    settings = config.Settings()
    assert settings.server_host == "127.0.0.1"
    assert "*" not in settings.cors_allow_origins and "http://localhost:3000" in settings.cors_allow_origins
