"""The public LLM API. Every script calls these; none calls a provider.

`judge` is the interesting one. A clause-versus-reference question is usually
easy and occasionally genuinely ambiguous, and paying a large model for all of
them wastes most of the money on the easy ones. So the cascade asks a small
model three cheap yes/no/unclear questions first and only escalates when the
small model is not confident -- and when it still is not confident after
escalating, the answer is `review`, never a guessed `pass`.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError

from prep.llm import cache, config as C, providers as P
from prep.llm.ledger import LEDGER, Call


class LLMUnavailable(RuntimeError):
    """Raised when a provider key is missing. Callers may catch this and fall
    back to the deterministic extractor rather than failing the run."""


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def complete(system: str, user: str, model: str, *, temperature: float = 0.0,
             max_tokens: int = 8000, task: str = "complete") -> Completion:
    """Raw chat call, metered.

    This is the entry point the tender extractor uses. It lives here rather
    than in a separate module so that every provider call -- including the
    expensive 12-tender extraction -- lands in the cost ledger. When it lived
    outside, the biggest spend of the whole pipeline was never recorded.
    """
    if not C.NEBIUS_API_KEY:
        raise LLMUnavailable(
            "NEBIUS_API_KEY is not set. Run with --backend rules for the "
            "offline extractor, or put the key in .env.")
    tier = C.Tier("RAW", "nebius", model)
    t0 = time.perf_counter()
    try:
        reply = P.nebius_chat(model, system, user, max_tokens=max_tokens,
                              temperature=temperature)
    except P.MissingKey as exc:
        raise LLMUnavailable(str(exc)) from exc
    ms = int((time.perf_counter() - t0) * 1000)
    LEDGER.record(Call(
        task=task, tier=tier.name, provider="nebius", model=model,
        input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
        cost_usd=C.cost_usd(model, reply.input_tokens, reply.output_tokens),
        latency_ms=ms, cache_hit=False))
    return Completion(reply.text, model, reply.input_tokens, reply.output_tokens)


def smoke_test() -> str:
    """Prove the key and model name are right before a run depends on them."""
    c = complete("Reply with JSON only.",
                 'Return {"ok": true, "lang": "<language of this word: omzet>"}',
                 C.SMALL.model, max_tokens=100, task="smoke")
    return f"{c.model}: {c.text.strip()[:120]}"


# ------------------------------------------------------------------ helpers
def softmax(scores: list[float], temp: float = None) -> list[float]:
    """Turn raw 0-100 micro-scores into probabilities."""
    t = temp if temp is not None else C.SOFTMAX_TEMP
    xs = [s / t for s in scores]
    m = max(xs) if xs else 0.0
    exps = [math.exp(x - m) for x in xs]
    total = sum(exps) or 1.0
    return [e / total for e in exps]


def parse_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    for cand in (text, *re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            continue
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _run(task: str, tier: C.Tier, payload: Any, fn, *, cacheable: bool = True):
    """Cache lookup, timing, cost and ledger write around one provider call."""
    key = cache.key_for(tier.model, task, payload)
    if cacheable:
        hit = cache.get(key)
        if hit is not None:
            LEDGER.record(Call(task=task, tier=tier.name, provider=tier.provider,
                               model=tier.model, cache_hit=True))
            return hit, True
    t0 = time.perf_counter()
    reply, value = fn()
    ms = int((time.perf_counter() - t0) * 1000)
    LEDGER.record(Call(
        task=task, tier=tier.name, provider=tier.provider, model=tier.model,
        input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
        cost_usd=C.cost_usd(tier.model, reply.input_tokens, reply.output_tokens),
        latency_ms=ms, cache_hit=False))
    if cacheable:
        cache.put(key, tier.model, task, value)
    return value, False


# -------------------------------------------------------------------- embed
def embed(texts: list[str], task: str = "embed") -> list[list[float]]:
    """Relevance triage. Cheapest tier; used to rank before anything reads."""
    if not texts:
        return []
    tier = C.EMBED

    def call():
        vecs, tokens = P.nebius_embed(tier.model, texts)
        return P.Reply("", tier.model, tokens, 0), vecs

    vecs, _ = _run(task, tier, texts, call)
    return vecs


# ----------------------------------------------------------------- classify
def classify(prompt: str, options: list[str], task: str = "classify") -> dict:
    """Pick one of `options`, with probabilities. SMALL tier."""
    tier = C.SMALL
    system = ("You are a precise classifier. Reply with JSON only: "
              '{"option": "<one of the options>", "confidence": 0-1}. '
              "Choose only from the options given.")
    user = f"{prompt}\n\nOptions: {json.dumps(options, ensure_ascii=False)}"

    def call():
        reply = P.nebius_chat(tier.model, system, user, max_tokens=120)
        data = parse_json(reply.text) or {}
        opt = data.get("option")
        if opt not in options:
            opt = options[0]
        conf = float(data.get("confidence", 0.5) or 0.5)
        probs = {o: (conf if o == opt else (1 - conf) / max(1, len(options) - 1))
                 for o in options}
        return reply, {"option": opt, "probs": probs}

    value, _ = _run(task, tier, {"prompt": prompt, "options": options}, call)
    return value


# ------------------------------------------------------------------ extract
def extract(pages_text: str, schema: Type[BaseModel], *, instructions: str = "",
            task: str = "extract") -> BaseModel:
    """Context-heavy structured extraction. SMART, with one retry.

    Retries on the fallback model rather than the same one: if a response fails
    validation the usual cause is the model, not the temperature.
    """
    tier = C.smart_tier()
    system = (instructions or
              "Extract the requested fields from the document. Quote verbatim. "
              "Invent nothing. Reply with JSON only.")
    js = schema.model_json_schema()
    user = f"{pages_text}\n\nReturn JSON matching this schema:\n{json.dumps(js)}"

    attempts = [tier]
    if tier.provider == "anthropic":
        attempts.append(C.SMART_FALLBACK)

    last: Optional[Exception] = None
    for t in attempts:
        def call(t=t):
            if t.provider == "anthropic":
                reply = P.anthropic_json(t.model, system, user, js)
            else:
                reply = P.nebius_chat(t.model, system, user, max_tokens=8000)
            return reply, parse_json(reply.text) or {}

        try:
            data, _ = _run(task, t, {"text": pages_text, "schema": js}, call)
            return schema(**data)
        except (ValidationError, TypeError, P.ProviderError) as exc:
            last = exc
            continue
    raise RuntimeError(f"extract failed after {len(attempts)} attempt(s): {last}")


# -------------------------------------------------------------------- judge
# Phrased as CLAIMS to be rated true or false, not as questions.
#
# Asked as questions ("Does the evidence satisfy the requirement?" /
# "Does it fail to?") the small model answered 100 to both -- it was scoring
# how relevant the question was, not whether it held. That made p_yes == p_no
# on obvious cases and escalated them to the expensive tier, which defeats the
# whole point of the cascade. Rating a claim gives it something to be wrong
# about, and the obvious cases now settle on SMALL.
MICRO = {
    "yes": "CLAIM: The supplier's evidence SATISFIES this requirement.",
    "no": "CLAIM: The supplier's evidence DOES NOT satisfy this requirement.",
    "unclear": "CLAIM: The evidence is insufficient to decide either way.",
}
MICRO_SYSTEM = (
    "You rate how likely a CLAIM is TRUE, given only the evidence shown. "
    'Reply with JSON only: {"p": <0-100>}, where 100 means certainly true and '
    "0 means certainly false. Judge the claim itself, not how relevant it is.")


def _micro(claim: str, requirement: str, reference: str) -> float:
    tier = C.SMALL
    user = (f"Requirement:\n{requirement}\n\nEvidence:\n{reference}\n\n{claim}")

    def call():
        reply = P.nebius_chat(tier.model, MICRO_SYSTEM, user, max_tokens=10)
        data = parse_json(reply.text) or {}
        try:
            p = float(data.get("p", 0))
        except (TypeError, ValueError):
            p = 0.0
        return reply, max(0.0, min(100.0, p))

    value, _ = _run("judge.micro", tier,
                    {"claim": claim, "req": requirement, "ref": reference}, call)
    return value


SMART_SYSTEM = (
    "You decide whether a tender requirement is satisfied by the supplier's "
    "evidence. Be strict: if the clause calls for a judgement a procurement "
    "officer would have to make, answer unclear rather than guessing. "
    'Reply with JSON only: {"answer":"yes|no|unclear","confidence":0-1}.')


def judge(requirement: str, reference: str, *, context: str = "") -> dict:
    """Cascade: three SMALL micro-scores, escalate to SMART only if uncertain."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        scores = list(pool.map(
            lambda kv: _micro(kv[1], requirement, reference), MICRO.items()))
    p_yes, p_no, p_unclear = softmax(scores)

    if p_yes >= C.T_PASS:
        return {"p_yes": p_yes, "p_no": p_no, "p_unclear": p_unclear,
                "decision": "pass", "tier_used": "small", "model": C.SMALL.model}
    if p_no >= C.T_GAP:
        return {"p_yes": p_yes, "p_no": p_no, "p_unclear": p_unclear,
                "decision": "gap", "tier_used": "small", "model": C.SMALL.model}

    if C.DISABLE_SMART:
        return {"p_yes": p_yes, "p_no": p_no, "p_unclear": p_unclear,
                "decision": "review", "tier_used": "small",
                "model": C.SMALL.model}

    tier = C.smart_tier()
    user = (f"Requirement clause:\n{requirement}\n\n"
            f"{('Surrounding context:' + chr(10) + context + chr(10) + chr(10)) if context else ''}"
            f"Supplier evidence:\n{reference}")

    def call():
        if tier.provider == "anthropic":
            reply = P.anthropic_json(tier.model, SMART_SYSTEM, user, {
                "type": "object",
                "properties": {"answer": {"type": "string",
                                          "enum": ["yes", "no", "unclear"]},
                               "confidence": {"type": "number"}},
                "required": ["answer", "confidence"]}, max_tokens=300)
        else:
            reply = P.nebius_chat(tier.model, SMART_SYSTEM, user, max_tokens=300)
        return reply, parse_json(reply.text) or {}

    data, _ = _run("judge.smart", tier,
                   {"req": requirement, "ref": reference, "ctx": context}, call)
    answer = str(data.get("answer", "unclear")).lower()
    try:
        conf = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    if answer == "yes" and conf >= C.T_SMART:
        decision = "pass"
    elif answer == "no" and conf >= C.T_SMART:
        decision = "gap"
    else:
        decision = "review"
    return {"p_yes": p_yes, "p_no": p_no, "p_unclear": p_unclear,
            "decision": decision, "tier_used": "smart", "model": tier.model,
            "smart_answer": answer, "smart_confidence": conf}


# ------------------------------------------------------------------ explain
def explain(facts: list[str] | str, *, task: str = "explain") -> str:
    """One short sentence a person can read. SMALL tier."""
    tier = C.SMALL
    body = facts if isinstance(facts, str) else "\n".join(f"- {f}" for f in facts)
    system = ('Write one plain sentence for a procurement manager. '
              'No hedging, no preamble. Reply as JSON: {"text": "..."}')

    def call():
        reply = P.nebius_chat(tier.model, system, body, max_tokens=160)
        data = parse_json(reply.text) or {}
        return reply, str(data.get("text", "")).strip()

    value, _ = _run(task, tier, body, call)
    return value
