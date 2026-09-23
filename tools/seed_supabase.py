"""Push the seed data into Supabase over PostgREST.

Bulk rows go through SECURITY DEFINER functions rather than direct table
writes, so seeding never needs the service role key on this machine. The
functions are dropped afterwards -- see tools/seed_supabase.py --teardown.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_ANON_KEY"]
HEAD = {"apikey": KEY, "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json"}

from prep import config as C          # noqa: E402
from prep.emit_sql import tender_uuid  # noqa: E402


def rpc(fn: str, payload) -> int:
    r = requests.post(f"{URL}/rest/v1/rpc/{fn}", headers=HEAD,
                      json={"payload": payload}, timeout=180)
    if r.status_code >= 300:
        raise SystemExit(f"{fn} failed {r.status_code}: {r.text[:400]}")
    return r.json()


def main() -> int:
    reqs = json.loads((C.DATA / "requirements.json").read_text())
    for r in reqs:
        r["tender_id"] = r.get("tender_id") or tender_uuid(r["ted_id"])
    print(f"requirements -> {rpc('seed_requirements', reqs)} rows")

    partners = json.loads((C.DATA / "partners.json").read_text())
    slim = [{"id": p["id"], "name": p["name"], "slug": p["slug"],
             "references": p.get("references", [])} for p in partners]
    print(f"partner references -> {rpc('seed_partners', slim)} rows "
          f"({len(slim)} companies)")

    by_slug = {p["slug"]: p["id"] for p in partners}
    facts = []
    fcsv = C.CURATED / "partner_facts.csv"
    if fcsv.exists():
        for row in csv.DictReader(fcsv.open(encoding="utf-8")):
            cid = by_slug.get(row["company_slug"])
            if cid:
                facts.append({"company_id": cid, "req_type": row["req_type"],
                              "key": row["key"], "value_text": row.get("value_text"),
                              "source_url": row.get("source_url"),
                              "source_quote": row.get("source_quote")})
    print(f"partner cert claims -> {rpc('seed_facts', facts)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
