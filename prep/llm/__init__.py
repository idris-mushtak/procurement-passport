"""Tiered LLM layer. Import from here, never from a provider SDK."""
from prep.llm.cache import stats as cache_stats
from prep.llm.config import (
    DISABLE_SMART, EMBED, PRICES_TODO, SMALL, SMART_FALLBACK, SMART_PROVIDER,
    T_GAP, T_PASS, T_SMART, cost_usd, smart_tier,
)
from prep.llm.core import (
    Completion, LLMUnavailable, classify, complete, embed, explain, extract,
    judge, parse_json, smoke_test, softmax,
)
from prep.llm.ledger import LEDGER

__all__ = [
    "embed", "classify", "extract", "judge", "explain", "softmax",
    "complete", "parse_json", "smoke_test", "LLMUnavailable", "Completion",
    "LEDGER", "cache_stats", "cost_usd", "smart_tier", "PRICES_TODO",
    "EMBED", "SMALL", "SMART_FALLBACK", "SMART_PROVIDER", "DISABLE_SMART",
    "T_PASS", "T_GAP", "T_SMART",
]
