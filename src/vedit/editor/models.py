"""Model registry for the AI editor: any model, from any Anthropic-compatible source.

The editor's agent is the Claude Agent SDK (Claude Code). Claude Code can talk to any
server that speaks the Anthropic Messages API, so a "provider" here is just
a base URL + API key (DeepSeek, OpenCode, OpenRouter, a LiteLLM proxy, ...), and a
"model" is a model id served by that provider.

Stored in ~/.aiklips/models.json (override the folder with $VEDIT_HOME), mode 0600
because it may hold API keys:

    {
      "providers": [
        {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/anthropic",
         "api_key": "sk-...",               # or leave empty and use api_key_env
         "api_key_env": "DEEPSEEK_API_KEY", # env var read when api_key is empty
         "env": {"API_TIMEOUT_MS": "600000"}}  # optional extra env for Claude Code
      ],
      "models": [
        {"provider": "deepseek", "id": "deepseek-chat", "label": "DeepSeek V3", "vision": false}
      ]
    }

Models are addressed by key "<provider>/<model id>". The built-in provider "anthropic"
uses the normal Claude Code login and needs no key.

A provider with "api": "openai" speaks the OpenAI Chat Completions API instead (ChatGPT models from OpenAI,
or any OpenAI-compatible server). Its base URL is the OpenAI-style root (https://api.openai.com/v1) and
Claude Code reaches it through the local translator in `openai_bridge.py`.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..library import home_dir

BUILTIN = "anthropic"
BUILTIN_PROVIDER = {"id": BUILTIN, "name": "Claude", "base_url": "", "api_key": "", "api_key_env": "", "env": {}, "api": "anthropic"}
APIS = ("anthropic", "openai")
BUILTIN_MODELS = [
    {"provider": BUILTIN, "id": "claude-opus-5-5", "label": "Opus 5.5 (best)", "vision": True},
    {"provider": BUILTIN, "id": "claude-sonnet-5-5", "label": "Sonnet 5.5 (faster, cheaper)", "vision": True},
    {"provider": BUILTIN, "id": "claude-haiku-5-5", "label": "Haiku 5.5 (cheapest)", "vision": True},
]
DEFAULT_KEY = f"{BUILTIN}/" + os.environ.get("VEDIT_MODEL", "claude-opus-5-5")

# Starting points for the "Add provider" wizard; any other Anthropic-compatible URL works too.
# Suggested model ids are hints only: providers rename models, so the wizard tests them.
TEMPLATES = [
    {"id": "openai", "name": "OpenAI (ChatGPT)", "api": "openai", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY",
     "blurb": "GPT models with your OpenAI API key (platform.openai.com billing, not a ChatGPT Plus plan).",
     "key_url": "https://platform.openai.com/api-keys", "docs_url": "https://platform.openai.com/docs/models",
     "suggested": []},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/anthropic", "api_key_env": "DEEPSEEK_API_KEY",
     "blurb": "Low-cost models with an official Anthropic-compatible API.",
     "key_url": "https://platform.deepseek.com/api_keys", "docs_url": "https://api-docs.deepseek.com/guides/anthropic_api",
     "suggested": [{"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "vision": False},
                   {"id": "deepseek-flash", "label": "DeepSeek Flash", "vision": False}]},
    {"id": "opencode", "name": "OpenCode", "base_url": "https://opencode.ai/inference/anthropic", "api_key_env": "OPENCODE_API_KEY",
     "blurb": "One key for many hosted models (OpenCode Zen).",
     "key_url": "https://opencode.ai/v2/docs/console/harnesses/claude-code/", "docs_url": "https://opencode.ai/docs/providers",
     "suggested": []},
    {"id": "custom", "name": "", "base_url": "", "api_key_env": "", "api": "anthropic",
     "blurb": "Any server that speaks the Anthropic Messages API (OpenRouter, LiteLLM…) or the OpenAI API (Groq, Ollama, LM Studio…).",
     "key_url": "", "docs_url": "", "suggested": []},
]


UA = "aiklips/1.0"  # some providers sit behind bot filters that reject Python's default agent


class RegistryError(ValueError):
    pass


def _path() -> Path:
    return home_dir() / "models.json"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def load() -> dict:
    data: dict = {"providers": [], "models": []}
    p = _path()
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    providers = [BUILTIN_PROVIDER] + [x for x in data.get("providers", []) if x.get("id") != BUILTIN]
    user_models = [m for m in data.get("models", []) if f"{m.get('provider')}/{m.get('id')}" not in {key(b) for b in BUILTIN_MODELS}]
    return {"providers": providers, "models": BUILTIN_MODELS + user_models}


def save(reg: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {"providers": [x for x in reg["providers"] if x["id"] != BUILTIN],
           "models": [m for m in reg["models"] if m not in BUILTIN_MODELS]}
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def key(m: dict) -> str:
    return f"{m['provider']}/{m['id']}"


def normalize_key(k: str | None) -> str:
    """Older chat.json files stored a bare Claude model id."""
    if not k:
        return DEFAULT_KEY
    return k if "/" in k else f"{BUILTIN}/{k}"


def provider(reg: dict, pid: str) -> dict | None:
    return next((p for p in reg["providers"] if p["id"] == pid), None)


def api_key(p: dict) -> str:
    return p.get("api_key") or (os.environ.get(p["api_key_env"], "") if p.get("api_key_env") else "")


def resolve(k: str, reg: dict | None = None) -> tuple[dict, dict]:
    reg = reg or load()
    k = normalize_key(k)
    m = next((m for m in reg["models"] if key(m) == k), None)
    if m is None:
        raise RegistryError(f"Unknown model {k}")
    p = provider(reg, m["provider"])
    if p is None:
        raise RegistryError(f"Model {k} has no provider {m['provider']}")
    return p, m


def env_for(k: str, reg: dict | None = None) -> dict[str, str]:
    """Environment for the Claude Code process so every request goes to the model's provider."""
    p, m = resolve(k, reg)
    if p["id"] == BUILTIN:
        return {}
    secret = api_key(p)
    if not secret and not (p.get("api") == "openai" and _local(p["base_url"])):
        where = f"set ${p['api_key_env']}" if p.get("api_key_env") else "add an API key"
        raise RegistryError(f"No API key for {p['name']}: {where} in Manage models.")
    mid = m["id"]
    base = p["base_url"].rstrip("/")
    if p.get("api") == "openai":  # Claude Code talks to the local translator; it holds the real key
        from . import openai_bridge

        base, secret = f"{openai_bridge.ensure_running()}/p/{p['id']}", openai_bridge.local_token()
    env = {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_AUTH_TOKEN": secret,
        "ANTHROPIC_API_KEY": secret,
        "ANTHROPIC_MODEL": mid,
        # Claude Code also makes background calls (titles, subagents) with its "small" models.
        "ANTHROPIC_DEFAULT_OPUS_MODEL": mid,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": mid,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": mid,
        "ANTHROPIC_SMALL_FAST_MODEL": mid,
        "CLAUDE_CODE_SUBAGENT_MODEL": mid,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    env.update({str(a): str(b) for a, b in (p.get("env") or {}).items()})
    return env


def _local(url: str) -> bool:
    return bool(re.match(r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:|/|$)", url))


def public(reg: dict | None = None) -> dict:
    """Registry for the browser: never includes the keys themselves."""
    reg = reg or load()
    providers = []
    for p in reg["providers"]:
        has = p["id"] == BUILTIN or bool(api_key(p)) or (p.get("api") == "openai" and _local(p["base_url"]))
        providers.append({"id": p["id"], "name": p["name"], "base_url": p["base_url"], "builtin": p["id"] == BUILTIN,
                          "api_key_env": p.get("api_key_env", ""), "key_stored": bool(p.get("api_key")), "ready": has,
                          "env": p.get("env") or {}, "api": p.get("api") or "anthropic"})
    models = [{**m, "key": key(m), "builtin": m in BUILTIN_MODELS} for m in reg["models"]]
    return {"providers": providers, "models": models, "templates": TEMPLATES}


# ---- edits ---------------------------------------------------------------------------
def upsert_provider(data: dict) -> dict:
    reg = load()
    name = str(data.get("name", "")).strip()
    base = str(data.get("base_url", "")).strip().rstrip("/")
    pid = _slug(str(data.get("id") or name))
    if not pid or not name:
        raise RegistryError("Provider needs a name.")
    if pid == BUILTIN:
        raise RegistryError("“anthropic” is the built-in Claude provider; pick another name.")
    cur0 = provider(reg, pid)
    api = str(data.get("api") or (cur0 or {}).get("api") or "anthropic")
    if api not in APIS:
        raise RegistryError("API format must be anthropic or openai.")
    if not re.match(r"^https?://", base):
        raise RegistryError("Base URL must start with http:// or https://")
    if api == "anthropic" and (base.endswith("/v1/messages") or base.endswith("/v1")):
        raise RegistryError("Leave /v1 off the base URL (Claude Code adds /v1/messages itself).")
    if api == "openai" and base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    env_name = str(data.get("api_key_env", "")).strip()
    if env_name and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", env_name):
        raise RegistryError("API key env var must be a variable name like DEEPSEEK_API_KEY.")
    extra = data.get("env") or {}
    if not isinstance(extra, dict):
        raise RegistryError("Extra env must be a JSON object.")
    cur = provider(reg, pid)
    new = {"id": pid, "name": name, "base_url": base, "api_key_env": env_name, "env": extra, "api": api,
           "api_key": cur.get("api_key", "") if cur else ""}
    if "api_key" in data and data["api_key"] is not None:  # "" clears a stored key; omitted keeps it
        new["api_key"] = str(data["api_key"]).strip()
    if not new["api_key"] and not env_name and not (api == "openai" and _local(base)):
        raise RegistryError("Give an API key or the name of an env var that holds it.")
    reg["providers"] = [new if p["id"] == pid else p for p in reg["providers"]] if cur else reg["providers"] + [new]
    save(reg)
    return new


def remove_provider(pid: str) -> None:
    if pid == BUILTIN:
        raise RegistryError("The built-in Claude provider can't be removed.")
    reg = load()
    reg["providers"] = [p for p in reg["providers"] if p["id"] != pid]
    reg["models"] = [m for m in reg["models"] if m["provider"] != pid]
    save(reg)


def add_model(data: dict) -> dict:
    reg = load()
    pid, mid = str(data.get("provider", "")), str(data.get("id", "")).strip()
    if provider(reg, pid) is None:
        raise RegistryError(f"Unknown provider {pid}")
    if not mid or any(c.isspace() for c in mid):
        raise RegistryError("Model id is required and can't contain spaces.")
    m = {"provider": pid, "id": mid, "label": str(data.get("label") or mid).strip(), "vision": bool(data.get("vision", False))}
    reg["models"] = [x for x in reg["models"] if key(x) != key(m)] + [m]
    save(reg)
    return m


def remove_model(k: str) -> None:
    reg = load()
    if any(key(b) == k for b in BUILTIN_MODELS):
        raise RegistryError("Built-in Claude models can't be removed.")
    reg["models"] = [m for m in reg["models"] if key(m) != k]
    save(reg)


def _get_json(url: str, secret: str) -> Any:
    req = urllib.request.Request(url, headers={"anthropic-version": "2023-06-01", "x-api-key": secret,
                                               "authorization": f"Bearer {secret}", "accept": "application/json", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


def _vision(x: dict) -> bool | None:
    """Does the provider say this model takes images? None when it doesn't say."""
    for mods in ((x.get("modalities") or {}).get("input"), (x.get("architecture") or {}).get("input_modalities"),
                 x.get("input_modalities")):
        if isinstance(mods, list):
            return "image" in mods
    caps = x.get("capabilities")
    if isinstance(caps, dict):
        for k in ("vision", "image_input", "images"):
            if k in caps:
                v = caps[k]
                return bool(v.get("supported", True) if isinstance(v, dict) else v)
    if str(x.get("id", "")).startswith("claude-"):
        return True
    return None


# OpenAI's /models lists everything (embeddings, speech, images...); the editor needs chat models with tool calls
_NOT_CHAT = re.compile(r"embed|whisper|tts|dall-e|davinci|babbage|moderation|transcribe|realtime|audio|image|"
                       r"search|sora|computer-use|instruct|codex", re.I)


def _openai_vision(mid: str) -> bool:
    return bool(re.match(r"(gpt-4o|gpt-4\.1|gpt-4\.5|gpt-5|o[1-9]|chatgpt-4o|gpt-[6-9])", mid)) and "mini-tts" not in mid and not mid.startswith("o1-mini")


def _pretty(mid: str) -> str:
    words = re.split(r"[-_/]", mid.split("/")[-1])
    return " ".join(w.upper() if re.fullmatch(r"v\d[\d.]*|[a-z]\d+", w) else w.capitalize() for w in words if w)


def list_models(pid: str) -> list[dict]:
    """Ask the provider which models it serves (Anthropic- or OpenAI-style /models endpoints)."""
    p = provider(load(), pid)
    if p is None:
        raise RegistryError(f"Unknown provider {pid}")
    if p["id"] == BUILTIN:
        return [dict(m) for m in BUILTIN_MODELS]
    secret = api_key(p)
    if not secret and not (p.get("api") == "openai" and _local(p["base_url"])):
        raise RegistryError("No API key set.")
    base = p["base_url"].rstrip("/")
    urls = [base + "/models"] if p.get("api") == "openai" else [base + "/v1/models", base + "/models"]
    if base.endswith("/anthropic"):  # e.g. DeepSeek serves the list on its OpenAI-style root
        root = base[: -len("/anthropic")]
        urls += [root + "/v1/models", root + "/models"]
    errors = []
    for url in dict.fromkeys(urls):
        try:
            data = _get_json(url, secret)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RegistryError(f"Invalid API key (HTTP {e.code})") from None
            errors.append(f"{e.code}")
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            errors.append(type(e).__name__)
            continue
        items = data.get("data") or data.get("models") if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        out = []
        for x in items:
            x = x if isinstance(x, dict) else {"id": str(x)}
            mid = str(x.get("id") or x.get("name") or "").strip()
            if not mid or str(x.get("type", "chat")).lower() in ("embedding", "embeddings", "image", "audio", "tts", "moderation"):
                continue
            if p.get("api") == "openai" and _NOT_CHAT.search(mid):
                continue
            if p.get("api") == "openai" and "openai.com" in base:
                x = {**x, "vision": _openai_vision(mid)}
            name = x.get("display_name") or (x.get("name") if x.get("name") and x.get("name") != mid else None)
            out.append({"id": mid, "label": str(name or _pretty(mid)), "vision": x["vision"] if "vision" in x else _vision(x)})
        if out:
            if p.get("api") == "openai" and "openai.com" in base:  # newest first: ids sort by family and date
                out.sort(key=lambda m: m["id"], reverse=True)
            return out
    raise RegistryError("This provider doesn't publish a model list" + (f" ({', '.join(errors)})" if errors else "") + ". Type a model id instead.")


def test(k: str) -> str:
    """Send a tiny request to a saved model's provider. Returns a short status or raises."""
    p, m = resolve(k)
    return test_model(p["id"], m["id"])


def test_model(pid: str, mid: str) -> str:
    """Like test(), for a model id that may not be saved yet (the wizard checks before adding)."""
    p = provider(load(), pid)
    if p is None:
        raise RegistryError(f"Unknown provider {pid}")
    m = {"id": mid}
    if p["id"] == BUILTIN:
        return "Uses your Claude Code login."
    if p.get("api") == "openai":
        from . import openai_bridge

        try:
            data = openai_bridge.complete(p["id"], {"model": mid, "max_tokens": 64,
                                                    "messages": [{"role": "user", "content": "Reply with: ok"}]})
        except openai_bridge.BridgeError as e:
            hint = {401: "Invalid API key", 403: "Key not allowed to use this model", 404: "Model or URL not found",
                    402: "Out of credit", 429: "Rate limited or out of quota"}.get(e.status)
            raise RegistryError(f"{hint + ' — ' if hint else ''}{e} (HTTP {e.status})") from None
        text = " ".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text").strip()
        return f"OK, {mid} replied: {text[:60] or '(empty)'}"
    secret = api_key(p)
    if not secret:
        raise RegistryError("No API key set.")
    body = json.dumps({"model": m["id"], "max_tokens": 8, "messages": [{"role": "user", "content": "Reply with: ok"}]}).encode()
    req = urllib.request.Request(p["base_url"].rstrip("/") + "/v1/messages", data=body, method="POST", headers={
        "content-type": "application/json", "anthropic-version": "2023-06-01",
        "x-api-key": secret, "authorization": f"Bearer {secret}", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data: Any = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            err = json.loads(raw).get("error") or {}
            detail = err.get("message") if isinstance(err, dict) else str(err)
        except (json.JSONDecodeError, AttributeError):
            detail = None
        detail = re.sub(r"\s*\(request[_ ]id[^)]*\)", "", detail or raw[:200]).strip()
        hint = {401: "Invalid API key", 403: "Key not allowed to use this model", 404: "Model or URL not found",
                402: "Out of credit", 429: "Rate limited"}.get(e.code)
        raise RegistryError(f"{hint + ' — ' if hint else ''}{detail} (HTTP {e.code})") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RegistryError(f"Can't reach {p['base_url']}: {getattr(e, 'reason', e)}") from None
    text = " ".join(c.get("text", "") for c in data.get("content", []) if isinstance(c, dict)).strip()
    return f"OK, {data.get('model') or m['id']} replied: {text[:60] or '(empty)'}"
