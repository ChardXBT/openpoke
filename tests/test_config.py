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
