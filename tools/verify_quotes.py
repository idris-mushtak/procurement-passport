"""Check that every extracted source_quote really occurs in the document pack.

This is the anti-hallucination test. A model asked for verbatim citations will
occasionally paraphrase, merge two clauses, or invent one outright -- and a
requirement whose quote cannot be found is a requirement the demo must not show,
because the UI's whole credibility rests on clicking through to the clause.

Matching is whitespace- and case-insensitive, and falls back to a token-overlap
score, so ligature and hyphenation noise from the PDF layer does not read as a
fabrication.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prep import config as C          # noqa: E402
from prep.docs import pages_for       # noqa: E402

VERBATIM, NEAR, MISSING = "verbatim", "near", "MISSING"


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r"[­‐-―]", "-", s)
    return " ".join(re.sub(r"[^a-z0-9€%.,:/'\- ]+", " ", s).split())


def tokens(s: str) -> Counter:
    return Counter(t for t in norm(s).split() if len(t) > 2)


def classify(quote: str, haystack: str) -> tuple[str, float]:
    nq, nh = norm(quote), norm(haystack)
    if nq and nq in nh:
        return VERBATIM, 1.0
    tq = tokens(quote)
    if not tq:
        return MISSING, 0.0
    th = tokens(haystack)
    overlap = sum(min(c, th[t]) for t, c in tq.items()) / sum(tq.values())
    if overlap >= 0.85:
        return NEAR, overlap
    return MISSING, overlap


def main() -> int:
    reqs = json.loads((C.DATA / "requirements.json").read_text())
    packs: dict[str, str] = {}
    for tid in {r["ted_id"] for r in reqs}:
        packs[tid] = "\n".join(p.text for p in pages_for(C.PACKS / tid))

    counts = Counter()
    bad = []
    for r in reqs:
        verdict, score = classify(r["source_quote"], packs.get(r["ted_id"], ""))
        counts[verdict] += 1
        r["_quote_check"] = verdict
        if verdict == MISSING:
            bad.append((r, score))

    total = len(reqs)
    for k in (VERBATIM, NEAR, MISSING):
        pct = 100.0 * counts[k] / total if total else 0
        print(f"  {k:9s} {counts[k]:3d}  ({pct:.0f}%)")

    if bad:
        print(f"\n{len(bad)} quote(s) not found in the pack -- do not ship these:")
        for r, score in sorted(bad, key=lambda x: x[1]):
            print(f"  {r['ted_id']} {r['req_type']}/{r['key']} "
                  f"(overlap {score:.2f}, confidence {r['confidence']})")
            print(f"     \"{r['source_quote'][:130]}\"")

    (C.DATA / "quote_check.json").write_text(json.dumps(reqs, indent=2, default=str))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
