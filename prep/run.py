"""prep.py -- one command that produces every seed file the demo needs.

    python -m prep.run all                  # everything, offline-capable
    python -m prep.run select               # choose the 12 demo tenders
    python -m prep.run packs                # download their document packs
    python -m prep.run extract --backend both
    python -m prep.run partners             # TED award notices -> candidates
    python -m prep.run facts                # verify partner certs on their sites
    python -m prep.run emit                 # write out/*.sql
    python -m prep.run smoke                # check the Nebius key and model name
    python -m prep.run report               # what got extracted, for spot-checking
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date

from prep import config as C
from prep import emit_sql, partners as partners_mod, select_tenders
from prep.canonical import apply as canonicalise
from prep.extract_requirements import extract_tender
from prep.tenderned import fetch_pack

REQ_JSON = C.DATA / "requirements.json"


def _selected() -> list[dict]:
    if not C.SELECTED_JSON.exists():
        raise SystemExit("run `python -m prep.run select` first")
    return json.loads(C.SELECTED_JSON.read_text())


def cmd_select(_) -> int:
    return select_tenders.main()


def cmd_packs(_) -> int:
    ok = 0
    for t in _selected():
        try:
            docs = fetch_pack(t["ted_id"], t["documents_url"])
            ok += 1
            print(f"  {t['ted_id']}  {len(docs)} documents")
        except Exception as exc:
            print(f"  {t['ted_id']}  FAILED {type(exc).__name__}: {exc}")
    print(f"{ok}/{len(_selected())} packs available")
    return 0


def cmd_extract(args) -> int:
    tenders = _selected()
    rows_out, total = [], 0
    for t in tenders:
        rows, meta = extract_tender(t["ted_id"], backend=args.backend)
        total += len(rows)
        tid = emit_sql.tender_uuid(t["ted_id"])
        for r in rows:
            d = r.model_dump()
            d["tender_id"] = tid
            d["ted_id"] = t["ted_id"]
            rows_out.append(d)
        # Extraction is what tells us whether combinations and subcontracting
        # are allowed, and those flags drive the level-6 rule -- so they have
        # to go back onto the tender record, not just into the log.
        t["joint_bids_allowed"] = meta["flags"].get("joint_bids_allowed", True)
        t["subcontracting_allowed"] = meta["flags"].get("subcontracting_allowed", True)
        t["pack_status"] = "fetched" if meta["pages"] else "unavailable"
        note = meta.get("llm") or meta.get("note") or ""
        print(f"  {t['ted_id']}  {len(rows):2d} requirements  "
              f"({meta['pages']} pages read)  {note}")

    # Collapse synonym keys onto one vocabulary before anything downstream
    # groups by key -- gap_summary does, and fragmented keys understate it.
    rows_out, stats = canonicalise(rows_out)
    print(f"  canonicalised: {stats['renamed']} keys renamed, "
          f"{stats['merged']} duplicates merged, "
          f"{stats['forced_review']} moved to review")

    from prep.llm.ledger import LEDGER
    LEDGER.flush()   # buffered rows would otherwise die with the process

    C.SELECTED_JSON.write_text(json.dumps(tenders, indent=2))
    REQ_JSON.write_text(json.dumps(rows_out, indent=2, default=str))
    print(f"{len(rows_out)} requirements -> {REQ_JSON}")
    return 0


def cmd_partners(_) -> int:
    return partners_mod.main()


def cmd_facts(_) -> int:
    from prep import partner_facts
    return partner_facts.main()


def cmd_emit(_) -> int:
    tenders = _selected()
    for t in tenders:
        t["id"] = emit_sql.tender_uuid(t["ted_id"])
        t["source_url"] = C.TED_NOTICE_HTML.format(t["ted_id"])
    (C.OUT / "04_tenders.sql").write_text(emit_sql.tenders_sql(tenders), encoding="utf-8")

    reqs = json.loads(REQ_JSON.read_text()) if REQ_JSON.exists() else []
    (C.OUT / "05_requirements.sql").write_text(
        emit_sql.requirements_sql(reqs), encoding="utf-8")

    pfile = C.DATA / "partners.json"
    plist = json.loads(pfile.read_text()) if pfile.exists() else []
    by_slug = {p["slug"]: p["id"] for p in plist}
    facts = []
    fcsv = C.CURATED / "partner_facts.csv"
    if fcsv.exists():
        for row in csv.DictReader(fcsv.open(encoding="utf-8")):
            cid = by_slug.get(row["company_slug"])
            if not cid:
                continue
            facts.append({
                "company_id": cid, "req_type": row["req_type"], "key": row["key"],
                "value_num": float(row["value_num"]) if row.get("value_num") else None,
                "value_text": row.get("value_text") or None,
                "source_url": row.get("source_url") or None,
                "source_quote": row.get("source_quote") or None,
            })
    (C.OUT / "06_partners.sql").write_text(
        emit_sql.partners_sql(plist, facts), encoding="utf-8")

    demo = json.loads((C.CURATED / "demo_company.json").read_text(encoding="utf-8"))
    (C.SQL / "03_demo_company.sql").write_text(
        emit_sql.demo_company_sql(demo), encoding="utf-8")

    print(f"wrote {C.SQL}/03_demo_company.sql ({demo['name']})")
    print(f"wrote {C.OUT}/04_tenders.sql ({len(tenders)} tenders)")
    print(f"wrote {C.OUT}/05_requirements.sql ({len(reqs)} requirements)")
    print(f"wrote {C.OUT}/06_partners.sql ({len(plist)} partners, {len(facts)} claimed facts)")
    return 0


def cmd_smoke(_) -> int:
    from prep import llm
    try:
        print(llm.smoke_test())
    except llm.LLMUnavailable as exc:
        print(exc)
        return 1
    return 0


def cmd_report(_) -> int:
    """The 3-minutes-per-tender spot check, on one screen."""
    reqs = json.loads(REQ_JSON.read_text()) if REQ_JSON.exists() else []
    by_tender: dict[str, list[dict]] = {}
    for r in reqs:
        by_tender.setdefault(r["ted_id"], []).append(r)
    for t in _selected():
        rows = by_tender.get(t["ted_id"], [])
        days = (date.fromisoformat(t["deadline"]) - C.TODAY).days if t.get("deadline") else None
        val = f"EUR {t['value_eur']:,.0f}" if t.get("value_eur") else "value unknown"
        print(f"\n=== {t['ted_id']}  {t['title'][:56]}")
        print(f"    {t['buyer'][:50]} | {val} | deadline {t.get('deadline')} ({days}d)")
        print(f"    joint_bids={t.get('joint_bids_allowed')} "
              f"subcontracting={t.get('subcontracting_allowed')}")
        if not rows:
            print("    (no requirements extracted -- needs a human)")
        for r in rows:
            th = (f"{r['threshold_num']:,.0f}" if r.get("threshold_num")
                  else (r.get("threshold_text") or "-"))
            print(f"    [{r['confidence']:6s}] {r['req_type']:14s} {r['key']:26s} "
                  f"{r['operator']:9s} {th:>12s}  p{r.get('page')}")
            print(f"             \"{r['source_quote'][:110]}\"")
    print(f"\n{len(reqs)} requirements across {len(by_tender)} tenders")
    return 0


def cmd_all(args) -> int:
    for step in (cmd_select, cmd_packs, cmd_extract, cmd_partners, cmd_facts, cmd_emit):
        print(f"\n--- {step.__name__.removeprefix('cmd_')}")
        rc = step(args)
        if rc:
            return rc
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="prep", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("select", "packs", "partners", "facts", "emit", "smoke", "report", "all"):
        sub.add_parser(name)
    ex = sub.add_parser("extract")
    ex.add_argument("--backend", choices=("rules", "llm", "both"), default="both",
                    help="rules = offline patterns; llm = Nebius; both = merge")
    for s in sub.choices.values():
        if not any(a.dest == "backend" for a in s._actions):
            s.set_defaults(backend="both")
    args = p.parse_args()
    return globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    sys.exit(main())
