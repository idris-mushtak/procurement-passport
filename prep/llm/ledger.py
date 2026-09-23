"""Cost ledger. Every call lands in Supabase `llm_calls`, including cache hits.

A cache hit is logged with cost 0 rather than not logged at all -- otherwise
the saving is invisible and `cost_summary` understates how many calls the run
actually needed.

Writes are best-effort: a demo must not die because the ledger is unreachable.
Rows buffer locally and flush together, and anything that cannot be sent is
kept in a JSONL file so the spend is still recoverable.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import requests

from prep.llm import config as C

FALLBACK = C.CACHE_DB.parent / ".llm_calls.jsonl"


@dataclass
class Call:
    task: str
    tier: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    cache_hit: bool = False


class Ledger:
    def __init__(self, flush_every: int = 25):
        self._rows: list[Call] = []
        self._lock = threading.Lock()
        self._flush_every = flush_every

    def record(self, call: Call) -> None:
        with self._lock:
            self._rows.append(call)
            due = len(self._rows) >= self._flush_every
        if due:
            self.flush()

    def flush(self) -> int:
        with self._lock:
            rows, self._rows = self._rows, []
        if not rows:
            return 0
        payload = [asdict(r) for r in rows]
        if C.SUPABASE_URL and C.SUPABASE_SERVICE_ROLE_KEY:
            try:
                r = requests.post(
                    f"{C.SUPABASE_URL}/rest/v1/llm_calls",
                    headers={
                        "apikey": C.SUPABASE_SERVICE_ROLE_KEY,
                        "Authorization": f"Bearer {C.SUPABASE_SERVICE_ROLE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    json=payload, timeout=30)
                if r.status_code < 300:
                    return len(payload)
            except requests.RequestException:
                pass
        with FALLBACK.open("a", encoding="utf-8") as fh:
            for row in payload:
                fh.write(json.dumps(row) + "\n")
        return len(payload)

    def summary(self) -> dict:
        """Local view of this process's spend, for the smoke test."""
        with self._lock:
            rows = list(self._rows)
        return {
            "buffered": len(rows),
            "cost_usd": round(sum(r.cost_usd for r in rows), 6),
            "tokens": sum(r.input_tokens + r.output_tokens for r in rows),
        }


LEDGER = Ledger()
