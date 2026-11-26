"""Owner-token authentication for every API route except health."""

import importlib
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

from server.config import Settings, ensure_owner_token


def test_owner_token_generated_once_and_persisted(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.delenv("OPENPOKE_OWNER_TOKEN", raising=False)
    monkeypatch.setenv("OPENPOKE_ENV_FILE", str(env_file))

    settings = Settings(owner_token=None)
    first = ensure_owner_token(settings)
    assert len(first) >= 32
    assert settings.owner_token == first
    assert os.environ["OPENPOKE_OWNER_TOKEN"] == first
    assert f"OPENPOKE_OWNER_TOKEN={first}" in env_file.read_text(encoding="utf-8")
    assert (env_file.stat().st_mode & 0o777) == 0o600

    monkeypatch.delenv("OPENPOKE_OWNER_TOKEN")
    second_settings = Settings(owner_token=None)
    assert ensure_owner_token(second_settings) == first


def test_owner_token_preserves_existing_env_lines(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENPOKE_FAKE_GMAIL=1\n# comment\n", encoding="utf-8")
    monkeypatch.delenv("OPENPOKE_OWNER_TOKEN", raising=False)
    monkeypatch.setenv("OPENPOKE_ENV_FILE", str(env_file))

    token = ensure_owner_token(Settings(owner_token=None))

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "OPENPOKE_FAKE_GMAIL=1" in lines and "# comment" in lines
    assert f"OPENPOKE_OWNER_TOKEN={token}" in lines


def test_api_requires_owner_token_except_health(monkeypatch):
    app_mod = importlib.import_module("server.app")

    monkeypatch.setattr(app_mod, "get_settings", lambda: SimpleNamespace(owner_token="test-owner-token"))
    client = TestClient(app_mod.app)

    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/meta").status_code == 401
    assert client.get("/api/v1/meta", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/meta?token=test-owner-token").status_code == 401
    assert client.get("/api/v1/meta", headers={"Authorization": "Bearer test-owner-token"}).status_code == 200


def test_api_auth_fails_closed_when_token_missing(monkeypatch):
    app_mod = importlib.import_module("server.app")

    monkeypatch.setattr(app_mod, "get_settings", lambda: SimpleNamespace(owner_token=None))
    client = TestClient(app_mod.app)

    assert client.get("/api/v1/meta").status_code == 401
    assert client.get("/api/v1/health").status_code == 200
