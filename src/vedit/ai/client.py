"""One entry point for every LLM call made by References and Generate.

The only AI we rely on is a chat-model subscription: the Claude Code login (through the
Claude Agent SDK), or any Anthropic-compatible provider from Manage models. So every
task here is "send text + a few images, get JSON back":

    result, usage = call("study", prompt, StyleDNA, images=[sheet1, sheet2], quality="balanced")

What this module takes care of, so callers don't have to:
- model routing per task tier (vision / planner / editor) and quality level
- structured output (JSON schema) + pydantic validation, one repair retry on bad JSON
- images inline as base64 (downscaled), dropped with a note for models without vision
- a global limit of 2 concurrent calls; exponential backoff on rate limits / overload.
  After the retries a `RateLimited` is raised so the job queue can pause, not fail.
- usage accounting (tokens, cost when the provider reports it)

Tests swap the network for a fake with `set_backend(fn)`.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from ..library import home_dir

T = TypeVar("T", bound=BaseModel)


class AIError(Exception):
    """An AI call failed in a way the user should see."""


class AIUnavailable(AIError):
    """No usable model: not signed in, no API key, Claude Code missing."""


class RateLimited(AIError):
    """Quota / rate limit / overload that outlasted our retries. The job should pause and retry later."""

    def __init__(self, msg: str, retry_after: float = 120.0):
        super().__init__(msg)
        self.retry_after = retry_after


# ---- routing ----------------------------------------------------------------------------

TIERS = {"study": "vision", "footage": "vision", "critic": "vision", "planner": "planner", "editor": "editor"}
QUALITY = {
    "best": {"vision": "anthropic/claude-opus-5-5", "planner": "anthropic/claude-opus-5-5", "editor": "anthropic/claude-opus-5-5"},
    "balanced": {"vision": "anthropic/claude-sonnet-5-5", "planner": "anthropic/claude-opus-5-5", "editor": "anthropic/claude-sonnet-5-5"},
    "economy": {"vision": "anthropic/claude-sonnet-5-5", "planner": "anthropic/claude-sonnet-5-5", "editor": "anthropic/claude-haiku-5-5"},
}
MAX_IMAGES = 4
IMAGE_MAX_PX = 1568
_SEM = threading.BoundedSemaphore(2)
_backend: Callable[..., dict] | None = None


def settings_path() -> Path:
    return home_dir() / "ai.json"


def settings() -> dict:
    """Per-tier model overrides: {"vision": "<provider>/<model>", "planner": ..., "editor": ...}."""
    try:
        return json.loads(settings_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict) -> dict:
    from ..editor import models as registry

    clean = {}
    for tier in ("vision", "planner", "editor"):
        v = str(data.get(tier) or "").strip()
        if v:
            registry.resolve(v)  # raises RegistryError for unknown models
            clean[tier] = registry.normalize_key(v)
    settings_path().write_text(json.dumps(clean, indent=2))
    return clean


def model_for(task: str, quality: str = "balanced") -> str:
    tier = TIERS.get(task, "editor")
    if os.environ.get("VEDIT_AI_MODEL"):
        return os.environ["VEDIT_AI_MODEL"]
    return settings().get(tier) or QUALITY.get(quality, QUALITY["balanced"])[tier]


def set_backend(fn: Callable[..., dict] | None) -> None:
    """Replace the real LLM with fn(task=, system=, prompt=, schema=, images=, model=) -> dict (tests)."""
    global _backend
    _backend = fn


# ---- usage ---------------------------------------------------------------------------------

def _record_usage(task: str, model: str, usage: dict) -> None:
    try:
        with (home_dir() / "ai-usage.jsonl").open("a") as f:
            f.write(json.dumps({"time": time.time(), "task": task, "model": model, **usage}) + "\n")
    except OSError:
        pass


def usage_stats(task: str | None = None, last: int = 200) -> dict:
    """Average seconds per call (per task), learned from real calls; feeds the wizard estimate."""
    try:
        lines = (home_dir() / "ai-usage.jsonl").read_text().splitlines()[-last:]
    except OSError:
        return {}
    out: dict[str, list[float]] = {}
    for ln in lines:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if task and d.get("task") != task:
            continue
        out.setdefault(d.get("task", "?"), []).append(float(d.get("seconds") or 0))
    return {k: round(sum(v) / len(v), 1) for k, v in out.items() if v}


# ---- images ----------------------------------------------------------------------------------

def _image_block(path: Path) -> dict:
    from PIL import Image

    im = Image.open(path)
    im = im.convert("RGB")
    im.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


# ---- the real call (Claude Agent SDK) ------------------------------------------------------

_RATE = re.compile(r"rate.?limit|429|overload|529|usage limit|quota|too many requests|capacity", re.I)
_AUTH = re.compile(r"auth|log ?in|sign ?in|api key|401|403|not found.*claude|claude code.*not", re.I)


def _classify(msg: str) -> AIError:
    if _RATE.search(msg):
        return RateLimited(msg)
    if _AUTH.search(msg):
        return AIUnavailable(msg + "  (Sign in once by running `claude` in a terminal, or check the provider key in Manage models.)")
    return AIError(msg)


def _log(line: str) -> None:
    try:
        with (home_dir() / "ai.log").open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line.rstrip() + "\n")
    except OSError:
        pass


async def _sdk(model_key: str, system: str, prompt: str, schema: dict, images: list[Path]) -> tuple[Any, dict]:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

    from ..editor import models as registry

    try:
        _, model = registry.resolve(model_key)
        env = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", **registry.env_for(model_key)}
    except registry.RegistryError as e:
        raise AIUnavailable(str(e)) from None
    content: list[dict] = [{"type": "text", "text": prompt}] + [_image_block(p) for p in images]

    async def stream():
        yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

    work = home_dir() / "ai-work"
    work.mkdir(exist_ok=True)
    opts = ClaudeAgentOptions(
        model=model["id"], system_prompt=system, tools=[], allowed_tools=[], setting_sources=[], cwd=str(work),
        env=env, max_turns=3, output_format={"type": "json_schema", "schema": schema},
        max_buffer_size=32 * 1024 * 1024, stderr=_log,
    )
    text, result, usage = [], None, {}
    async for msg in query(prompt=stream(), options=opts):
        if isinstance(msg, AssistantMessage):
            if msg.error:
                raise _classify(str(msg.error))
            text += [b.text for b in msg.content if isinstance(b, TextBlock)]
        elif isinstance(msg, ResultMessage):
            u = msg.usage or {}
            usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                     "cache_read": u.get("cache_read_input_tokens"), "cost_usd": msg.total_cost_usd}
            if msg.is_error:
                raise _classify(" ".join(map(str, msg.errors or [])) or str(msg.result or msg.subtype) + f" {msg.api_error_status or ''}")
            result = msg.structured_output if msg.structured_output is not None else (msg.result or "\n".join(text))
    return result, usage


def _parse(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if m:
        s = m.group(1)
    start = min([i for i in (s.find("{"), s.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in the reply")
    return json.JSONDecoder().raw_decode(s[start:])[0]


def _once(task: str, model_key: str, system: str, prompt: str, schema: dict, images: list[Path]) -> tuple[Any, dict]:
    if _backend is not None:
        return _backend(task=task, system=system, prompt=prompt, schema=schema, images=images, model=model_key), {}
    try:
        return asyncio.run(_sdk(model_key, system, prompt, schema, images))
    except AIError:
        raise
    except ImportError as e:
        raise AIUnavailable(f"Claude Agent SDK missing: {e}") from None
    except Exception as e:  # CLINotFoundError, ProcessError, CLIConnectionError ...
        raise _classify(f"{type(e).__name__}: {e}") from None


def vision_ok(model_key: str) -> bool:
    if _backend is not None:
        return True
    from ..editor import models as registry

    try:
        return bool(registry.resolve(model_key)[1].get("vision"))
    except registry.RegistryError:
        return False


def call(task: str, prompt: str, out: type[T], *, system: str, images: list[Path] | None = None,
         quality: str = "balanced", retries: int = 3) -> tuple[T, dict]:
    """Run one task and return (validated output, usage). Raises AIError / AIUnavailable / RateLimited."""
    model_key = model_for(task, quality)
    imgs = [Path(p) for p in (images or []) if Path(p).exists()][:MAX_IMAGES]
    if imgs and not vision_ok(model_key):
        prompt += "\n\n(The images could not be attached because this model has no vision. Work from the numbers and text only.)"
        imgs = []
    schema = out.model_json_schema()
    attempt_prompt, delay, last_err = prompt, 8.0, ""
    for attempt in range(retries + 1):
        t0 = time.time()
        with _SEM:
            try:
                raw, usage = _once(task, model_key, system, attempt_prompt, schema, imgs)
            except RateLimited as e:
                last_err = str(e)
                if attempt == retries:
                    raise
                time.sleep(delay + random.uniform(0, delay / 2))
                delay *= 3
                continue
        usage = {**usage, "seconds": round(time.time() - t0, 2)}
        _record_usage(task, model_key, usage)
        try:
            return out.model_validate(_parse(raw)), usage
        except (ValidationError, ValueError) as e:
            last_err = str(e)[:1500]
            if attempt >= 1:  # one repair round is enough; a second bad answer is a real failure
                break
            attempt_prompt = prompt + f"\n\nYour previous answer was invalid:\n{last_err}\nReturn corrected JSON that matches the schema exactly."
    raise AIError(f"The AI returned an invalid {task} result: {last_err[:400]}")
