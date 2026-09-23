"""Reference implementation of the gap engine, in Python, over the prep output.

This is NOT the production engine -- that is Person B's Supabase Edge Function
in TypeScript. This exists for two reasons:

  1. Person A cannot tune the demo company without seeing what /evaluate would
     say about it. ("Tune demo company values for a clean story", 1:50-2:30.)
  2. It pins down the rules as executable behaviour, so the TS port has
     something to match rather than a table in a design doc.

It reads exactly what the SQL seeds contain, so if this produces a clean demo
the data is good.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prep import config as C  # noqa: E402

LADDER = {1: ("Evidence", 7), 2: ("Admin", 21), 3: ("People", 60),
          4: ("Certification", 150), 5: ("Track record", None),
          6: ("Hard blocker", None)}
BID_WRITING_MARGIN_DAYS = 5

# Gap level per requirement type, from the design doc's rules table.
BASE_LEVEL = {"certification": 4, "insurance": 2, "staff_language": 3,
              "turnover": 5, "references": 5, "local_presence": 5}

# Not every "certification" is a six-month audit. Dutch tenders ask for a pile
# of administrative attestations -- Gedragsverklaring Aanbesteden, a
# Belastingdienst payment statement, a Handelsregister extract -- which you
# request and receive in days. Scoring those at level 4 makes tenders look
# unreachable that a week of paperwork would open, which is the opposite of the
# error this product exists to prevent.
ADMIN_ATTESTATIONS = {
    "gva", "gedragsverklaring", "gedragsverklaring_aanbesteden",
    "belastingdienst_verklaring", "belastingdienst", "handelsregister",
    "handelsregister_uittreksel", "kvk_uittreksel", "uea", "espd",
    "eigen_verklaring", "verklaring_belastingdienst",
}


def level_for(req: dict) -> int:
    """Ladder level before the structural-block rule is applied."""
    if req["req_type"] == "certification" and req["key"] in ADMIN_ATTESTATIONS:
        return 1
    return BASE_LEVEL.get(req["req_type"], 5)


def load() -> tuple[list[dict], list[dict], dict, list[dict]]:
    tenders = json.loads(C.SELECTED_JSON.read_text())
    reqs = json.loads((C.DATA / "requirements.json").read_text())
    demo = json.loads((C.CURATED / "demo_company.json").read_text())
    partners = json.loads((C.DATA / "partners.json").read_text())
    return tenders, reqs, demo, partners


def capabilities(company: dict) -> dict[tuple[str, str], dict]:
    return {(c["req_type"], c["key"]): c for c in company.get("capabilities", [])}


def count_references(company: dict, window_years: int | None,
                     public_required: bool, min_value: float | None,
                     as_of: date) -> int:
    n = 0
    for r in company.get("references", []):
        if public_required and not r.get("public_sector"):
            continue
        if min_value and (r.get("value_eur") or 0) < min_value:
            continue
        if window_years and r.get("end_date"):
            end = date.fromisoformat(r["end_date"])
            if (as_of - end).days > window_years * 365:
                continue
        n += 1
    return n


def check(req: dict, company: dict, deadline: date | None, as_of: date) -> dict:
    """pass / gap / review for one requirement. Never returns pass on a
    judgement call -- that is the false-eligible rate the demo claims."""
    caps = capabilities(company)
    rt, key, op = req["req_type"], req["key"], req["operator"]

    if rt == "other" or not req.get("knockout", True):
        return {"status": "review", "reason": "judgement call -- shown, not scored"}

    if rt == "references":
        if key == "reference_count":
            have = count_references(company, req.get("window_years"),
                                    req.get("public_sector_required", False),
                                    None, as_of)
            need = int(req.get("threshold_num") or 1)
            if have >= need:
                return {"status": "pass", "reason": f"{have} qualifying references, {need} required"}
            return {"status": "gap", "reason": f"{have} qualifying references, {need} required"}
        return {"status": "review", "reason": "reference value rule needs a human"}

    cap = caps.get((rt, key))

    if op == "exists":
        if cap is None:
            return {"status": "gap", "reason": f"no {key} on file"}
        if cap.get("valid_until") and deadline:
            if date.fromisoformat(cap["valid_until"]) < deadline:
                return {"status": "gap",
                        "reason": f"{key} expires {cap['valid_until']}, before the deadline"}
        return {"status": "pass", "reason": f"{key} held ({cap.get('trust')})"}

    if op in ("gte", "count_gte"):
        if cap is None or cap.get("value_num") is None:
            return {"status": "gap", "reason": f"no {key} on file"}
        have, need = float(cap["value_num"]), float(req.get("threshold_num") or 0)
        if have >= need:
            return {"status": "pass", "reason": f"{have:,.0f} >= {need:,.0f}"}
        return {"status": "gap", "reason": f"{have:,.0f} < {need:,.0f} required"}

    return {"status": "review", "reason": f"operator {op} not auto-checked"}


def gap_level(req: dict, tender: dict, company: dict) -> int:
    level = level_for(req)
    # A level-5 gap on a tender that forbids both combinations and
    # subcontracting cannot be closed at all: that is level 6.
    if level == 5 and not tender.get("joint_bids_allowed", True) \
            and not tender.get("subcontracting_allowed", True):
        return 6
    return level


def fixable(level: int, deadline: date | None, as_of: date) -> bool:
    max_days = LADDER[level][1]
    if max_days is None or deadline is None:
        return False
    return max_days <= (deadline - as_of).days - BID_WRITING_MARGIN_DAYS


def evaluate(company: dict, tenders: list[dict], reqs: list[dict],
             as_of: date, partners: list[dict] | None = None,
             facts: dict[str, set[str]] | None = None) -> list[dict]:
    by_tender: dict[str, list[dict]] = {}
    for r in reqs:
        by_tender.setdefault(r["ted_id"], []).append(r)

    out = []
    for t in tenders:
        deadline = date.fromisoformat(t["deadline"]) if t.get("deadline") else None
        rows, met, gaps, reviews = [], 0, [], 0
        for req in by_tender.get(t["ted_id"], []):
            res = check(req, company, deadline, as_of)
            if res["status"] == "pass":
                met += 1
            elif res["status"] == "review":
                reviews += 1
            else:
                lvl = gap_level(req, t, company)
                res.update({"gap_level": lvl, "label": LADDER[lvl][0],
                            "fixable_in_time": fixable(lvl, deadline, as_of),
                            "key": req["key"], "req_type": req["req_type"]})
                gaps.append(res)
            rows.append({**req, **res})

        total = met + len(gaps)
        # Label order matters: first match wins (design doc, "Tender label").
        if any(g["gap_level"] == 6 for g in gaps):
            label = "out_of_reach"
        elif not gaps:
            label = "ready"
        elif all(g["fixable_in_time"] for g in gaps):
            label = "reachable_fix"
        else:
            # "Reachable: partner" is a claim that a specific partner closes
            # the remaining gaps. Assert it only after checking that one does;
            # otherwise the honest answer is Future.
            remaining = [g for g in gaps if not g["fixable_in_time"]]
            label = "future"
            if company.get("prefs", {}).get("allow_partners", True) and partners:
                for p in partners:
                    if set(partner_covers(p, remaining, facts or {}, as_of, t)) >= \
                            {g["key"] for g in remaining}:
                        label = "reachable_partner"
                        break
        out.append({"tender": t, "rows": rows, "met": met, "total": total,
                    "gaps": gaps, "reviews": reviews, "label": label})
    return out


def partner_covers(partner: dict, gaps: list[dict], facts: dict[str, set[str]],
                   as_of: date, tender: dict | None = None) -> list[str]:
    """Which of these gaps this partner can actually close on THIS tender.

    Two constraints the design doc states and it is easy to drop:
      * capacity gaps (references, turnover) can only be borrowed in a
        combination -- if joint bids are barred, the partner cannot help;
      * capability gaps (certification, staff, presence) are covered by putting
        the partner on the work -- which needs subcontracting to be allowed.
    Ignoring these produces a demo where everything is reachable, which is the
    one answer a procurement tool must never give by default.
    """
    covered: list[str] = []
    held = facts.get(partner["slug"], set())
    joint_ok = (tender or {}).get("joint_bids_allowed", True)
    sub_ok = (tender or {}).get("subcontracting_allowed", True)
    tender_cpv = {c[:2] for c in (tender or {}).get("cpv", []) if c}

    for g in gaps:
        rt = g["req_type"]
        if rt == "certification":
            if sub_ok and g["key"] in held:
                covered.append(g["key"])
        elif rt in ("staff_language", "local_presence"):
            if sub_ok and g["key"] in held:
                covered.append(g["key"])
        elif rt in ("references", "turnover"):
            if not joint_ok:
                continue
            # A reference only counts if it is the same kind of work. Matching
            # the CPV division is a coarse test, but it is the one the award
            # data can actually support.
            relevant = [r for r in partner.get("references", [])
                        if r.get("public_sector")
                        and (not tender_cpv or (r.get("cpv") or "")[:2] in tender_cpv)]
            need = int(g.get("threshold_num") or 1) if rt == "references" else 1
            if len(relevant) >= need:
                covered.append(g["key"])
    return covered


def main() -> int:
    tenders, reqs, demo, partners = load()
    as_of = C.TODAY

    facts: dict[str, set[str]] = {}
    import csv
    fcsv = C.CURATED / "partner_facts.csv"
    if fcsv.exists():
        for row in csv.DictReader(fcsv.open(encoding="utf-8")):
            facts.setdefault(row["company_slug"], set()).add(row["key"])

    results = evaluate(demo, tenders, reqs, as_of, partners, facts)

    print(f"=== 1. PROFILE  {demo['name']}  (as of {as_of})")
    for c in demo["capabilities"]:
        v = c.get("value_num") or c.get("value_text")
        print(f"    [{c['trust']:8s}] {c['key']:26s} {v}")
    print(f"    {len(demo['references'])} references "
          f"({sum(1 for r in demo['references'] if r['public_sector'])} public sector)")

    print("\n=== 2. REACHABLE TENDERS")
    reach = [r for r in results
             if r["label"] in ("ready", "reachable_fix", "reachable_partner")]
    value = sum(r["tender"].get("value_eur") or 0 for r in reach)
    print(f"    {len(reach)} of {len(results)} tenders within reach, worth EUR {value:,.0f}")
    for r in sorted(results, key=lambda x: x["label"]):
        t = r["tender"]
        days = (date.fromisoformat(t["deadline"]) - as_of).days if t.get("deadline") else None
        val = f"{t['value_eur']:,.0f}" if t.get("value_eur") else "-"
        print(f"    {r['label']:18s} {r['met']:2d}/{r['total']:<2d} met  "
              f"EUR {val:>12s}  {days:>3}d  {t['title'][:40]}")

    print("\n=== 3. TENDER DETAIL (first tender with gaps)")
    detail = next((r for r in results if r["gaps"]), results[0])
    print(f"    {detail['tender']['title']}")
    for row in detail["rows"]:
        mark = {"pass": "PASS", "gap": "GAP ", "review": "REV "}[row["status"]]
        extra = ""
        if row["status"] == "gap":
            extra = (f"  level {row['gap_level']} ({row['label']}), "
                     f"fixable in time: {row['fixable_in_time']}")
        print(f"    {mark} {row['req_type']:14s} {row['key']:26s} {row['reason']}{extra}")
        print(f"         clause p{row.get('page')}: \"{row['source_quote'][:90]}\"")

    print("\n=== 4. GAP VALUE (what each gap costs, alone)")
    agg: dict[str, dict] = {}
    for r in results:
        for g in r["gaps"]:
            a = agg.setdefault(g["key"], {"blocked": 0, "value": 0.0,
                                          "alone": 0, "alone_value": 0.0,
                                          "level": g["gap_level"]})
            a["blocked"] += 1
            a["value"] += r["tender"].get("value_eur") or 0
            if len(r["gaps"]) == 1:
                a["alone"] += 1
                a["alone_value"] += r["tender"].get("value_eur") or 0
    for key, a in sorted(agg.items(), key=lambda kv: -kv[1]["alone_value"]):
        print(f"    {key:26s} blocks {a['blocked']:2d} tenders "
              f"(EUR {a['value']:>12,.0f})  |  unlocks alone: {a['alone']} "
              f"(EUR {a['alone_value']:,.0f})  [ladder {a['level']}]")

    print("\n=== 5. PARTNER CASE")
    target = next((r for r in results if r["gaps"] and r["label"] != "out_of_reach"), None)
    if target:
        gaps = target["gaps"]
        print(f"    {target['tender']['title'][:60]}")
        print(f"    You alone: {target['met']} of {target['total']} "
              f"({', '.join(g['key'] for g in gaps)} missing)")
        ranked = []
        for p in partners:
            cov = partner_covers(p, gaps, facts, as_of, target["tender"])
            if cov:
                coverage = len(cov) / len(gaps)
                evidence = 1.0 if p.get("public_awards", 0) >= 2 else 0.5
                ranked.append((0.5 * coverage + 0.25 * evidence, p, cov))
        ranked.sort(key=lambda x: -x[0])
        if not ranked:
            print("    no seeded partner covers these gaps "
                  "-- add partner facts in data/curated/partner_facts.csv")
        for score, p, cov in ranked[:3]:
            joint = target["met"] + len(cov)
            print(f"    + {p['name'][:34]:34s} score {score:.2f}  covers {cov}  "
                  f"-> joint {joint}/{target['total']}  "
                  f"({p['public_awards']} public awards on TED)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
