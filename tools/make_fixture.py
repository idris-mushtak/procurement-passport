"""Export the seed data as a JSON fixture the Deno tests can load.

The point is a cross-language check: the TypeScript Edge Function and the
Python reference engine must agree, tender for tender, on the same input.
A port that silently drifts is worse than no port.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prep import config as C
from prep.emit_sql import tender_uuid

def main() -> int:
    tenders = json.loads(C.SELECTED_JSON.read_text())
    reqs = json.loads((C.DATA / "requirements.json").read_text())
    demo = json.loads((C.CURATED / "demo_company.json").read_text())
    partners = json.loads((C.DATA / "partners.json").read_text())

    facts = {}
    import csv
    f = C.CURATED / "partner_facts.csv"
    if f.exists():
        for row in csv.DictReader(f.open(encoding="utf-8")):
            facts.setdefault(row["company_slug"], []).append({
                "req_type": row["req_type"], "key": row["key"],
                "value_text": row.get("value_text"), "value_num": None,
                "valid_until": None, "trust": "claim",
                "source_url": row.get("source_url"),
            })

    fixture = {
        "as_of": C.TODAY.isoformat(),
        "tenders": [{
            "id": tender_uuid(t["ted_id"]), "ted_id": t["ted_id"],
            "title": t["title"], "buyer": t["buyer"],
            "value_eur": t.get("value_eur"), "deadline": t.get("deadline"),
            "cpv": t.get("cpv") or [],
            "joint_bids_allowed": t.get("joint_bids_allowed", True),
            "subcontracting_allowed": t.get("subcontracting_allowed", True),
        } for t in tenders],
        "requirements": [{
            "id": f"{r['ted_id']}::{r['req_type']}::{r['key']}",
            "tender_id": tender_uuid(r["ted_id"]),
            "req_type": r["req_type"], "key": r["key"], "operator": r["operator"],
            "threshold_num": r.get("threshold_num"),
            "threshold_text": r.get("threshold_text"),
            "window_years": r.get("window_years"),
            "public_sector_required": r.get("public_sector_required", False),
            "knockout": r.get("knockout", True),
            "source_quote": r["source_quote"], "page": r.get("page"),
            "confidence": r.get("confidence", "medium"),
        } for r in reqs],
        "company": {
            "id": demo["id"], "name": demo["name"], "prefs": demo["prefs"],
            "capabilities": [{
                "company_id": demo["id"], "req_type": c["req_type"], "key": c["key"],
                "value_num": c.get("value_num"), "value_text": c.get("value_text"),
                "valid_until": c.get("valid_until"), "trust": c.get("trust", "verified"),
                "source_url": None,
            } for c in demo["capabilities"]],
            "references": [{
                "company_id": demo["id"], "buyer": r["buyer"],
                "public_sector": r["public_sector"], "cpv": r.get("cpv"),
                "value_eur": r.get("value_eur"), "end_date": r.get("end_date"),
                "trust": r.get("trust", "verified"), "source_url": None,
            } for r in demo["references"]],
        },
        "partners": [{
            "id": p["id"], "name": p["name"], "prefs": {},
            "capabilities": [{**c, "company_id": p["id"]}
                             for c in facts.get(p["slug"], [])],
            "references": [{
                "company_id": p["id"], "buyer": r["buyer"],
                "public_sector": r.get("public_sector", True), "cpv": r.get("cpv"),
                "value_eur": r.get("value_eur"), "end_date": r.get("end_date"),
                "trust": "official", "source_url": r.get("source_url"),
            } for r in p.get("references", [])],
            "awards": [],
        } for p in partners],
    }
    out = C.DATA / "fixture.json"
    out.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"wrote {out.relative_to(C.ROOT)} "
          f"({len(fixture['tenders'])} tenders, {len(fixture['requirements'])} reqs, "
          f"{len(fixture['partners'])} partners)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
