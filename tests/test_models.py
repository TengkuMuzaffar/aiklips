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
    if os.name != "nt":  # Windows has no POSIX permission bits
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


def _http_error(code):
    import urllib.error
    return urllib.error.HTTPError("u", code, "x", {}, None)


def test_list_models_anthropic_format(monkeypatch):
    reg.upsert_provider({"name": "p", "base_url": "https://x.example/anthropic", "api_key": "k"})
    seen = []

    def fake(url, key):
        seen.append(url)
        return {"data": [{"id": "m-pro", "display_name": "M Pro"}, {"id": "embed-1", "type": "embedding"},
                         {"id": "m-v4-flash", "modalities": {"input": ["text", "image"]}}]}
    monkeypatch.setattr(reg, "_get_json", fake)
    ms = reg.list_models("p")
    assert seen == ["https://x.example/anthropic/v1/models"]
    assert ms == [{"id": "m-pro", "label": "M Pro", "vision": None}, {"id": "m-v4-flash", "label": "M V4 Flash", "vision": True}]


def test_list_models_falls_back_to_openai_root(monkeypatch):
    reg.upsert_provider({"name": "p", "base_url": "https://x.example/anthropic", "api_key": "k"})

    def fake(url, key):
        if url != "https://x.example/models":
            raise _http_error(404)
        return {"object": "list", "data": [{"id": "deepseek-v4-pro", "object": "model"}]}
    monkeypatch.setattr(reg, "_get_json", fake)
    assert [m["id"] for m in reg.list_models("p")] == ["deepseek-v4-pro"]


def test_list_models_bad_key_and_no_list(monkeypatch):
    reg.upsert_provider({"name": "p", "base_url": "https://x.example", "api_key": "k"})
    monkeypatch.setattr(reg, "_get_json", lambda u, k: (_ for _ in ()).throw(_http_error(401)))
    with pytest.raises(reg.RegistryError, match="Invalid API key"):
        reg.list_models("p")
    monkeypatch.setattr(reg, "_get_json", lambda u, k: (_ for _ in ()).throw(_http_error(404)))
    with pytest.raises(reg.RegistryError, match="Type a model id"):
        reg.list_models("p")


def test_generate_roles_follow_a_non_claude_chat_model(home, monkeypatch):
    """Only DeepSeek set up: Generate's roles nobody picked a model for use it too, not a Claude login the user
    may not have. A Claude chat model keeps the quality tiers; a role picked by hand always wins."""
    import json

    from vedit.ai import client as ai

    monkeypatch.delenv("VEDIT_AI_MODEL", raising=False)
    ai.settings_path().write_text(json.dumps({"chat": "deepseek/deepseek-chat"}))
    assert {ai.model_for(t) for t in ("study", "planner", "editor", "auditor")} == {"deepseek/deepseek-chat"}
    assert {r["effective"] for r in ai.roles()} == {"deepseek/deepseek-chat"}
    ai.settings_path().write_text(json.dumps({"chat": "deepseek/deepseek-chat", "planner": "anthropic/claude-opus-5-5"}))
    assert ai.model_for("planner") == "anthropic/claude-opus-5-5"
    ai.settings_path().write_text(json.dumps({"chat": "anthropic/claude-sonnet-5-5"}))
    assert ai.model_for("planner") == ai.QUALITY["balanced"]["planner"]
