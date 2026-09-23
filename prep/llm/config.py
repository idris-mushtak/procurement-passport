"""Tier configuration for the LLM layer.

The point of tiers is cost shape, not model worship: the volume steps (triage,
per-clause judgments) run on a small open model, and the expensive model is
reserved for the few calls where context actually decides the answer. Every
value here is overridable by environment variable so the split can be retuned
without a code change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from prep import config as base

load_dotenv(base.ROOT / ".env")

# --- providers -------------------------------------------------------------
NEBIUS_BASE_URL = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# anthropic | nebius. Defaults to nebius: the Anthropic path is built and
# tested, but this project runs without an Anthropic key today.
SMART_PROVIDER = os.getenv("SMART_PROVIDER", "nebius").lower()
# Forces everything through SMALL; anything the cascade cannot settle becomes
# `review` rather than escalating. Use it to price a run's floor.
DISABLE_SMART = os.getenv("DISABLE_SMART", "false").lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Tier:
    name: str
    provider: str
    model: str


# Model IDs verified against GET /v1/models on 2026-09-23 -- every one is an
# exact match, no guessing.
EMBED = Tier("EMBED", "nebius", os.getenv("PP_MODEL_EMBED", "Qwen/Qwen3-Embedding-8B"))
SMALL = Tier("SMALL", "nebius", os.getenv("PP_MODEL_SMALL", "Qwen/Qwen3-30B-A3B-Instruct-2507"))
SMART_ANTHROPIC = Tier("SMART", "anthropic", os.getenv("PP_MODEL_SMART", "claude-fable-5"))
SMART_FALLBACK = Tier("SMART_FALLBACK", "nebius",
                      os.getenv("PP_MODEL_SMART_FALLBACK", "deepseek-ai/DeepSeek-V4-Pro"))


def smart_tier() -> Tier:
    """Which tier SMART resolves to right now."""
    if SMART_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        return SMART_ANTHROPIC
    return SMART_FALLBACK


# --- prices, USD per 1M tokens --------------------------------------------
# (input, output). Nebius prices change; re-check them in the console before
# quoting a cost on stage.
PRICES: dict[str, tuple[float, float]] = {
    "Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),
    "Qwen/Qwen3-Embedding-8B": (0.01, 0.0),
    "claude-fable-5": (10.0, 50.0),
    # TODO(idris): fill from the Nebius console -- these are placeholders and
    # any cost figure that uses them is an estimate, not a measurement.
    "deepseek-ai/DeepSeek-V4-Pro": (0.0, 0.0),
    "Qwen/Qwen3.5-397B-A17B": (0.0, 0.0),
}
PRICES_TODO = [m for m, p in PRICES.items() if p == (0.0, 0.0)]


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 8)


# --- cascade thresholds ----------------------------------------------------
T_PASS = float(os.getenv("PP_T_PASS", "0.80"))
T_GAP = float(os.getenv("PP_T_GAP", "0.70"))
T_SMART = float(os.getenv("PP_T_SMART", "0.80"))
# softmax(score / SOFTMAX_TEMP) over the three micro-scorer outputs
SOFTMAX_TEMP = float(os.getenv("PP_SOFTMAX_TEMP", "20"))

# --- runtime ---------------------------------------------------------------
CONCURRENCY = int(os.getenv("PP_LLM_CONCURRENCY", "16"))
TIMEOUT = float(os.getenv("PP_LLM_TIMEOUT", "150"))
MAX_RETRIES = int(os.getenv("PP_LLM_RETRIES", "4"))
CACHE_DB = base.ROOT / "prep" / ".cache.db"

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
