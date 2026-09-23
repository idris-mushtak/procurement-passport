"""Live smoke test for the LLM layer. Hits real providers; costs a fraction of a cent.

    python -m prep.smoke_test

Proves four things before anyone builds on top: the keys work, the model IDs
are real, each tier returns the shape the code expects, and the spend is
actually being recorded rather than merely computed.
"""
from __future__ import annotations

import sys
import time

from pydantic import BaseModel, Field

from prep.llm import (
    LEDGER, PRICES_TODO, SMALL, classify, embed, explain, extract, judge,
    smart_tier,
)
from prep.llm import config as C
from prep.llm.ledger import FALLBACK


class Eis(BaseModel):
    req_type: str = Field(description="turnover | certification | insurance | other")
    key: str
    threshold_num: float | None = None
    source_quote: str


def step(n: int, name: str, fn):
    t0 = time.perf_counter()
    try:
        out = fn()
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {n}. {name:<12} OK    {ms:7.0f} ms   {str(out)[:88]}")
        return True
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {n}. {name:<12} FAIL  {ms:7.0f} ms   {type(exc).__name__}: {str(exc)[:88]}")
        return False


def main() -> int:
    print(f"providers   SMALL={SMALL.model}")
    print(f"            SMART={smart_tier().model} (provider {smart_tier().provider})")
    print(f"            EMBED={C.EMBED.model}")
    print(f"thresholds  T_PASS={C.T_PASS} T_GAP={C.T_GAP} T_SMART={C.T_SMART}\n")

    ok = []
    ok.append(step(1, "embed", lambda: f"{len(embed(['ISO 27001 certificaat'])[0])} dims"))
    ok.append(step(2, "classify", lambda: classify(
        "Is this clause about insurance or turnover? "
        "'Inschrijver beschikt over een bedrijfsaansprakelijkheidsverzekering "
        "van minimaal EUR 1.250.000 per gebeurtenis.'",
        ["insurance", "turnover"])["option"]))
    ok.append(step(3, "judge", lambda: judge(
        "Inschrijver beschikt over een geldig ISO 27001-certificaat.",
        "Het bedrijf is sinds 2024 ISO 27001 gecertificeerd (certificaat geldig tot 2027).",
    )))
    ok.append(step(4, "extract", lambda: extract(
        "3.2.1 Verzekering. Inschrijver beschikt over een "
        "bedrijfsaansprakelijkheidsverzekering van ten minste EUR 1.250.000 "
        "per gebeurtenis.", Eis,
        instructions="Extract the single knock-out requirement. Quote verbatim.",
    ).key))
    ok.append(step(5, "explain", lambda: explain([
        "gap: ISO 27001 missing", "blocks 10 tenders", "worth EUR 63M"])))

    print("\nspend this run (before flush):", LEDGER.summary())
    sent = LEDGER.flush()
    where = "Supabase llm_calls" if (C.SUPABASE_URL and C.SUPABASE_SERVICE_ROLE_KEY) \
        else f"local {FALLBACK.name} (SUPABASE_URL not set)"
    print(f"flushed {sent} ledger rows -> {where}")

    if PRICES_TODO:
        print(f"\nTODO: fill prices for {', '.join(PRICES_TODO)} in prep/llm/config.py "
              "-- any cost shown for these is 0, not measured.")

    failed = ok.count(False)
    print(f"\n{len(ok) - failed}/{len(ok)} steps passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
