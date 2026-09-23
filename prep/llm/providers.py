"""Provider adapters. Nothing outside this file talks to a vendor SDK.

Both adapters return the same shape -- text plus token counts -- so the tier
logic above never branches on provider.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Optional

from prep.llm import config as C


class ProviderError(RuntimeError):
    pass


class MissingKey(ProviderError):
    pass


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


def _retry(fn, *, what: str):
    """Exponential backoff with jitter on 429 and 5xx."""
    last: Optional[Exception] = None
    for attempt in range(C.MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:  # SDKs raise their own hierarchies
            last = exc
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None)
            retriable = status in (408, 409, 429, 500, 502, 503, 504) or status is None
            if not retriable or attempt == C.MAX_RETRIES - 1:
                raise ProviderError(f"{what}: {type(exc).__name__}: {exc}") from exc
            time.sleep(min(2 ** attempt + random.random(), 20))
    raise ProviderError(f"{what}: {last}")


# ------------------------------------------------------------------ nebius
_nebius = None


def nebius():
    global _nebius
    if _nebius is None:
        if not C.NEBIUS_API_KEY:
            raise MissingKey("NEBIUS_API_KEY is not set")
        from openai import OpenAI
        _nebius = OpenAI(base_url=C.NEBIUS_BASE_URL, api_key=C.NEBIUS_API_KEY,
                         timeout=C.TIMEOUT, max_retries=0)  # we retry ourselves
    return _nebius


def nebius_chat(model: str, system: str, user: str, *, json_mode: bool = True,
                max_tokens: int = 2000, temperature: float = 0.0) -> Reply:
    kwargs: dict[str, Any] = {
        "model": model, "temperature": temperature, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    r = _retry(lambda: nebius().chat.completions.create(**kwargs), what=f"nebius {model}")
    u = r.usage
    return Reply(r.choices[0].message.content or "", model,
                 getattr(u, "prompt_tokens", 0) or 0,
                 getattr(u, "completion_tokens", 0) or 0)


def nebius_embed(model: str, texts: list[str]) -> tuple[list[list[float]], int]:
    r = _retry(lambda: nebius().embeddings.create(model=model, input=texts),
               what=f"nebius embed {model}")
    tokens = getattr(getattr(r, "usage", None), "prompt_tokens", 0) or 0
    return [d.embedding for d in r.data], tokens


# --------------------------------------------------------------- anthropic
_anthropic = None


def anthropic():
    global _anthropic
    if _anthropic is None:
        if not C.ANTHROPIC_API_KEY:
            raise MissingKey("ANTHROPIC_API_KEY is not set")
        import anthropic as sdk
        _anthropic = sdk.Anthropic(api_key=C.ANTHROPIC_API_KEY, timeout=C.TIMEOUT,
                                   max_retries=0)
    return _anthropic


def anthropic_json(model: str, system: str, user: str, schema: dict,
                   *, tool_name: str = "emit", max_tokens: int = 8000) -> Reply:
    """Structured output via tool use -- the SDK's supported way to force a
    shape, rather than asking for JSON in prose and hoping."""
    tool = {"name": tool_name, "description": "Return the result.",
            "input_schema": schema}
    r = _retry(lambda: anthropic().messages.create(
        model=model, max_tokens=max_tokens, system=system,
        tools=[tool], tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user}],
    ), what=f"anthropic {model}")
    import json as _json
    payload = next((b.input for b in r.content if getattr(b, "type", "") == "tool_use"), None)
    return Reply(_json.dumps(payload or {}), model,
                 r.usage.input_tokens, r.usage.output_tokens)
