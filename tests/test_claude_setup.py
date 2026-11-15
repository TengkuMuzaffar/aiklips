import json
import stat

from vedit.editor import claude_setup


def _fake_cli(tmp_path, payload):
    f = tmp_path / "claude"
    f.write_text("#!/bin/sh\necho '" + json.dumps(payload) + "'\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return str(f)


def test_status_logged_in_keeps_only_safe_fields(tmp_path, monkeypatch):
    cli = _fake_cli(tmp_path, {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max",
                               "email": "someone@example.com", "orgName": "Org", "orgId": "x"})
    monkeypatch.setattr(claude_setup, "cli_path", lambda: (cli, "system"))
    s = claude_setup.status()
    assert s["installed"] and s["loggedIn"] and s["subscriptionType"] == "max"
    assert "email" not in s and "orgName" not in s and "orgId" not in s


def test_status_logged_out_and_env_key(tmp_path, monkeypatch):
    cli = _fake_cli(tmp_path, {"loggedIn": False, "authMethod": "none"})
    monkeypatch.setattr(claude_setup, "cli_path", lambda: (cli, "system"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert claude_setup.status()["loggedIn"] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert claude_setup.status()["loggedIn"] is True


def test_not_installed(monkeypatch):
    monkeypatch.setattr(claude_setup, "cli_path", lambda: (None, None))
    s = claude_setup.status()
    assert not s["installed"] and not s["loggedIn"] and "install" in s["install_command"]
    r = claude_setup.login()
    assert r["opened"] is False and "error" in r
