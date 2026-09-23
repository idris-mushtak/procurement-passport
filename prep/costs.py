"""What the last run cost.

    python -m prep.costs              # totals by task and tier
    python -m prep.costs --since 1h   # only the last hour
    python -m prep.costs --calls      # every individual call
    python -m prep.costs --reset      # archive the local ledger and start fresh

Reads Supabase `llm_calls` when the service role key is set, and the local
`prep/.llm_calls.jsonl` otherwise. Cache hits are counted but cost nothing, so
the saving is visible instead of merely implied.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from prep.llm import config as C
from prep.llm.ledger import FALLBACK


def parse_since(text: str | None) -> datetime | None:
    if not text:
        return None
    m = re.fullmatch(r"(\d+)\s*([mhd])", text.strip().lower())
    if not m:
        raise SystemExit("--since takes forms like 30m, 2h, 7d")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
    return datetime.now(timezone.utc) - delta


def from_supabase(since: datetime | None) -> list[dict] | None:
    if not (C.SUPABASE_URL and C.SUPABASE_SERVICE_ROLE_KEY):
        return None
    params = {"select": "*", "order": "created_at.desc", "limit": "10000"}
    if since:
        params["created_at"] = f"gte.{since.isoformat()}"
    try:
        r = requests.get(
            f"{C.SUPABASE_URL}/rest/v1/llm_calls",
            headers={"apikey": C.SUPABASE_SERVICE_ROLE_KEY,
                     "Authorization": f"Bearer {C.SUPABASE_SERVICE_ROLE_KEY}"},
            params=params, timeout=40)
        return r.json() if r.status_code < 300 else None
    except requests.RequestException:
        return None


def from_local(since: datetime | None) -> list[dict]:
    if not FALLBACK.exists():
        return []
    rows = []
    for line in FALLBACK.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # The local ledger has no timestamp per row, so --since cannot filter it.
    return rows


def money(v: float) -> str:
    if v == 0:
        return "$0"
    if v < 0.01:
        return f"${v:.6f}".rstrip("0")
    return f"${v:,.4f}"


def main() -> int:
    ap = argparse.ArgumentParser(prog="prep.costs", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="30m, 2h, 7d (Supabase source only)")
    ap.add_argument("--calls", action="store_true", help="list every call")
    ap.add_argument("--reset", action="store_true", help="archive the local ledger")
    args = ap.parse_args()

    if args.reset:
        if FALLBACK.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = FALLBACK.with_suffix(f".{stamp}.jsonl")
            shutil.move(FALLBACK, dest)
            print(f"archived to {dest.name}")
        else:
            print("nothing to archive")
        return 0

    since = parse_since(args.since)
    rows = from_supabase(since)
    source = "Supabase llm_calls"
    if rows is None:
        rows = from_local(since)
        source = f"local {FALLBACK.name}"
        if args.since:
            print("note: --since needs the Supabase ledger; showing everything local.\n")

    if not rows:
        print(f"No calls recorded yet ({source}).")
        print("Run `python -m prep.smoke_test` or an extraction to generate some.")
        return 0

    by = defaultdict(lambda: {"calls": 0, "hits": 0, "tin": 0, "tout": 0,
                              "cost": 0.0, "ms": 0})
    for r in rows:
        k = (r.get("task", "?"), r.get("tier", "?"), r.get("model", "?"))
        a = by[k]
        a["calls"] += 1
        a["hits"] += 1 if r.get("cache_hit") else 0
        a["tin"] += int(r.get("input_tokens") or 0)
        a["tout"] += int(r.get("output_tokens") or 0)
        a["cost"] += float(r.get("cost_usd") or 0)
        a["ms"] += int(r.get("latency_ms") or 0)

    total_cost = sum(a["cost"] for a in by.values())
    total_calls = sum(a["calls"] for a in by.values())
    total_hits = sum(a["hits"] for a in by.values())
    billed = total_calls - total_hits

    print(f"source: {source}" + (f"   since: {args.since}" if since else ""))
    print()
    print(f"  {'task':<14}{'tier':<16}{'calls':>6}{'cached':>7}"
          f"{'tokens':>10}{'avg ms':>8}{'cost':>12}")
    print("  " + "-" * 73)
    for (task, tier, model), a in sorted(by.items(), key=lambda kv: -kv[1]["cost"]):
        avg = a["ms"] // max(1, a["calls"] - a["hits"]) if a["calls"] > a["hits"] else 0
        print(f"  {task:<14}{tier:<16}{a['calls']:>6}{a['hits']:>7}"
              f"{a['tin'] + a['tout']:>10}{avg:>8}{money(a['cost']):>12}")
    print("  " + "-" * 73)
    print(f"  {'TOTAL':<30}{total_calls:>6}{total_hits:>7}"
          f"{sum(a['tin'] + a['tout'] for a in by.values()):>10}{'':>8}"
          f"{money(total_cost):>12}")

    print()
    if billed:
        print(f"  {billed} billed call{'s' if billed != 1 else ''}, "
              f"{money(total_cost / billed)} each on average")
    if total_hits:
        print(f"  {total_hits} served from cache at no cost "
              f"({100 * total_hits / total_calls:.0f}% of calls)")

    models = {m for (_, _, m) in by}
    missing = sorted(models & set(C.PRICES_TODO))
    if missing:
        print()
        print("  WARNING: no price on file for " + ", ".join(missing))
        print("  Those rows are counted as $0, so this total is a floor, not the bill.")
        print("  Fill PRICES in prep/llm/config.py from the Nebius console.")

    if source.startswith("local"):
        print()
        print("  Writing locally because SUPABASE_SERVICE_ROLE_KEY is unset.")
        print("  Add it to .env and these land in llm_calls / cost_summary instead.")

    if args.calls:
        print("\n  every call:")
        for r in rows:
            flag = "cache" if r.get("cache_hit") else "     "
            print(f"    {flag} {r.get('task','?'):<14}{r.get('model','?'):<38}"
                  f"{(r.get('input_tokens') or 0) + (r.get('output_tokens') or 0):>7} tok"
                  f"{r.get('latency_ms') or 0:>7} ms  {money(float(r.get('cost_usd') or 0))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
