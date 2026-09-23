"""Three-way comparison on identical input: no model, small model, our model.

The judging criterion asks what the open-model choice was measured against.
This runs the same page set from the same tenders through each configuration
and records requirements found, how many quotes could be located verbatim in
the source pack, tokens, cost and wall-clock latency.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prep import config as C                      # noqa: E402
from prep import rules_extract                    # noqa: E402
from prep.docs import as_prompt_block, pages_for  # noqa: E402
from prep.extract_requirements import SYSTEM, _validate  # noqa: E402
from prep.llm import providers as P               # noqa: E402
from prep.llm import config as LC                 # noqa: E402

spec = importlib.util.spec_from_file_location("vq", ROOT / "tools/verify_quotes.py")
vq = importlib.util.module_from_spec(spec); spec.loader.exec_module(vq)

TENDERS = ["648631-2026", "617228-2026", "647200-2026"]
MODELS = [
    ("Qwen3-30B-A3B", "Qwen/Qwen3-30B-A3B-Instruct-2507"),
    ("DeepSeek-V4.1-Flash", "deepseek-ai/DeepSeek-V4.1-Flash"),
    ("DeepSeek-V4-Pro", "deepseek-ai/DeepSeek-V4-Pro"),
]


def cite_rate(rows, haystack):
    """Share of quotes findable in the pack, or None when nothing was returned.

    An empty response is a reliability failure, not a citation failure. Scoring
    it 0% conflates "the model invented quotes" with "the model said nothing",
    which are different defects with different fixes.
    """
    if not rows:
        return None
    ok = sum(1 for r in rows
             if vq.classify(r.source_quote, haystack)[0] != vq.MISSING)
    return ok / len(rows)


def mean(vals):
    real = [v for v in vals if v is not None]
    return sum(real) / len(real) if real else 0.0


def main() -> int:
    results = {}

    # Baseline: no model at all.
    rule_total, rule_cite, rule_ms = 0, [], 0
    for tid in TENDERS:
        pages = pages_for(C.PACKS / tid)
        hay = "\n".join(p.text for p in pages)
        t0 = time.perf_counter()
        rows = rules_extract.extract(tid, pages)
        rule_ms += int((time.perf_counter() - t0) * 1000)
        rule_total += len(rows)
        rule_cite.append(cite_rate(rows, hay))
    results["patterns only (no model)"] = {
        "requirements": rule_total, "cite": mean(rule_cite), "empty_runs": sum(1 for v in rule_cite if v is None),
        "tokens": 0, "cost": 0.0, "ms": rule_ms, "model": "-",
    }
    print(f"  patterns only          {rule_total:>3} reqs  "
          f"cite {mean(rule_cite)*100:5.1f}%  "
          f"{rule_ms:>6} ms  $0")

    for label, model in MODELS:
        total, cites, tin, tout, ms = 0, [], 0, 0, 0
        for tid in TENDERS:
            pages = pages_for(C.PACKS / tid)
            hay = "\n".join(p.text for p in pages)
            user = (f"Aanbesteding {tid}. Fragmenten uit de aanbestedingsstukken:\n\n"
                    f"{as_prompt_block(pages)}\n\nGeef de geschiktheidseisen als JSON.")
            t0 = time.perf_counter()
            try:
                reply = P.nebius_chat(model, SYSTEM, user, max_tokens=8000)
            except Exception as exc:
                print(f"  {label:<22} FAILED on {tid}: {type(exc).__name__}")
                break
            ms += int((time.perf_counter() - t0) * 1000)
            tin += reply.input_tokens; tout += reply.output_tokens
            from prep.llm.core import parse_json
            rows, _ = _validate(tid, parse_json(reply.text) or {})
            total += len(rows)
            cites.append(cite_rate(rows, hay))
        if not cites:
            continue
        cost = LC.cost_usd(model, tin, tout)
        results[label] = {"requirements": total, "cite": mean(cites),
                          "empty_runs": sum(1 for v in cites if v is None),
                          "tokens": tin + tout, "cost": cost, "ms": ms, "model": model}
        print(f"  {label:<22} {total:>3} reqs  cite {mean(cites)*100:5.1f}%  "
              f"({sum(1 for v in cites if v is None)} empty)  "
              f"{ms:>6} ms  {tin+tout:>6} tok  ${cost:.6f}")

    (ROOT / "data" / "bench_models.json").write_text(
        json.dumps({"tenders": TENDERS, "results": results}, indent=2))
    print(f"\nwritten to data/bench_models.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
