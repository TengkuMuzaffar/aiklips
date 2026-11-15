import json
import os
import stat

import pytest

from vedit.editor import models as reg


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEDIT_HOME", str(tmp_path))
    monkeypatch.delenv("TESTPROV_KEY", raising=False)
    return tmp_path


def test_builtin_claude_always_present():
    pub = reg.public()
    assert pub["providers"][0]["id"] == "anthropic" and pub["providers"][0]["ready"]
    assert any(m["key"] == "anthropic/claude-opus-5-5" for m in pub["models"])
    assert reg.env_for("anthropic/claude-sonnet-5-5") == {}
    assert reg.normalize_key("claude-haiku-5-5") == "anthropic/claude-haiku-5-5"


def test_add_provider_and_model_with_stored_key(home):
    reg.upsert_provider({"name": "Deep Seek", "base_url": "https://api.deepseek.com/anthropic/", "api_key": "sk-secret"})
    reg.add_model({"provider": "deep-seek", "id": "deepseek-chat", "label": "DS"})
    env = reg.env_for("deep-seek/deepseek-chat")
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-secret" and env["ANTHROPIC_MODEL"] == "deepseek-chat"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "deepseek-chat"
    f = home / "models.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert [x["id"] for x in json.loads(f.read_text())["providers"]] == ["deep-seek"]  # built-ins not persisted
    assert "sk-secret" not in json.dumps(reg.public())  # never sent to the browser


def test_key_from_env_and_missing_key(monkeypatch):
    reg.upsert_provider({"name": "testprov", "base_url": "http://localhost:4000", "api_key_env": "TESTPROV_KEY", "env": {"API_TIMEOUT_MS": "9"}})
    reg.add_model({"provider": "testprov", "id": "m1", "vision": True})
    assert not reg.public()["providers"][1]["ready"]
    with pytest.raises(reg.RegistryError, match="No API key"):
        reg.env_for("testprov/m1")
    monkeypatch.setenv("TESTPROV_KEY", "k")
    env = reg.env_for("testprov/m1")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "k" and env["API_TIMEOUT_MS"] == "9"


def test_editing_keeps_saved_key_unless_cleared():
    reg.upsert_provider({"name": "p", "base_url": "https://x.example", "api_key": "abc", "api_key_env": "P_KEY"})
    reg.upsert_provider({"id": "p", "name": "p", "base_url": "https://y.example", "api_key_env": "P_KEY"})
    assert reg.provider(reg.load(), "p")["api_key"] == "abc"
    reg.upsert_provider({"id": "p", "name": "p", "base_url": "https://y.example", "api_key_env": "P_KEY", "api_key": ""})
    assert reg.provider(reg.load(), "p")["api_key"] == ""


@pytest.mark.parametrize("body,msg", [
    ({"name": "x", "base_url": "ftp://a"}, "http"),
    ({"name": "x", "base_url": "https://a/v1", "api_key": "k"}, "/v1"),
    ({"name": "x", "base_url": "https://a"}, "API key"),
    ({"name": "anthropic", "base_url": "https://a", "api_key": "k"}, "built-in"),
])
def test_provider_validation(body, msg):
    with pytest.raises(reg.RegistryError, match=msg):
        reg.upsert_provider(body)


def test_remove_provider_removes_its_models_and_builtins_are_protected():
    reg.upsert_provider({"name": "p", "base_url": "https://x.example", "api_key": "k"})
    reg.add_model({"provider": "p", "id": "m"})
    reg.remove_provider("p")
    assert not any(m["provider"] == "p" for m in reg.load()["models"])
    with pytest.raises(reg.RegistryError):
        reg.remove_provider("anthropic")
    with pytest.raises(reg.RegistryError):
        reg.remove_model("anthropic/claude-opus-5-5")

