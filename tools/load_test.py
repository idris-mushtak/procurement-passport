"""Load every seed file into a throwaway Postgres and assert the demo data is
actually queryable.

`validate_sql.py` only proves the SQL parses. This proves it RUNS: constraints
hold, foreign keys resolve, the gap_summary view compiles, and the numbers the
demo quotes come back from real SQL rather than from Python. It spins up a
private Postgres via pgserver, so it needs no Docker and no Supabase project.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ROOT / "sql" / "01_schema.sql",
    ROOT / "sql" / "02_ladder.sql",
    ROOT / "sql" / "03_demo_company.sql",
    ROOT / "out" / "04_tenders.sql",
    ROOT / "out" / "05_requirements.sql",
    ROOT / "out" / "06_partners.sql",
    ROOT / "sql" / "08_llm.sql",
    ROOT / "sql" / "07_rls.sql",
]

# Supabase ships these roles; stock Postgres does not. Creating them lets the
# RLS file be verified here instead of failing for the first time in the
# Supabase SQL editor.
SUPABASE_ROLES = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon')
    then create role anon nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated')
    then create role authenticated nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role')
    then create role service_role nologin noinherit bypassrls; end if;
end $$;
"""

CHECKS = [
    ("tenders",              "select count(*) from tenders"),
    ("requirements",         "select count(*) from requirements"),
    ("  with a clause cite", "select count(*) from requirements where source_quote <> '' and page is not null"),
    ("companies (partners+demo)", "select count(*) from companies"),
    ("partner references",   "select count(*) from company_references"),
    ("llm_calls table",      "select count(*) from llm_calls"),
    ("judgments table",      "select count(*) from judgments"),
    ("demo capabilities",    "select count(*) from capabilities where company_id = '11111111-1111-1111-1111-111111111111'"),
    ("partner cert claims",  "select count(*) from capabilities where trust = 'claim'"),
    ("ladder levels",        "select count(*) from ladder"),
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pp-pg-"))
    print(f"starting postgres in {tmp} ...")
    db = pgserver.get_server(str(tmp))
    try:
        db.psql(SUPABASE_ROLES)
        for f in FILES:
            # ON_ERROR_STOP matters: without it psql prints the error, carries
            # on, and the load "succeeds" with rows quietly missing.
            sql = "\\set ON_ERROR_STOP on\n" + f.read_text(encoding="utf-8")
            try:
                db.psql(sql)
                print(f"  LOADED  {f.relative_to(ROOT)}")
            except Exception as exc:
                msg = str(exc).strip().splitlines()
                print(f"  FAILED  {f.relative_to(ROOT)}")
                for line in msg[:6]:
                    print(f"          {line}")
                return 1

        print("\nrow counts:")
        for label, q in CHECKS:
            out = db.psql(f"copy ({q}) to stdout").strip()
            print(f"  {label:28s} {out}")

        # The view is the piece the dashboard reads; make sure it compiles and
        # returns nothing rather than erroring on an empty results table.
        db.psql("copy (select count(*) from gap_summary) to stdout")
        print("  gap_summary view            compiles (empty until /evaluate runs)")

        rls = db.psql(
            "copy (select count(*) from pg_tables where schemaname='public' "
            "and rowsecurity) to stdout").strip()
        pol = db.psql(
            "copy (select count(*) from pg_policies where schemaname='public') "
            "to stdout").strip()
        print(f"  tables with RLS enabled     {rls}")
        fns = db.psql(
            "copy (select count(*) from pg_proc where proname = 'update_prefs' "
            "and prosecdef) to stdout").strip()
        print(f"  update_prefs (security definer) {fns}")
        db.psql("copy (select count(*) from cost_summary) to stdout")
        print("  cost_summary view           compiles")
        print(f"  read policies               {pol}")

        print("\nintegrity:")
        orphans = db.psql(
            "copy (select count(*) from requirements r "
            "left join tenders t on t.id = r.tender_id where t.id is null) to stdout").strip()
        print(f"  requirements with no tender  {orphans}")
        dupes = db.psql(
            "copy (select count(*) from (select ted_id from tenders "
            "group by ted_id having count(*) > 1) x) to stdout").strip()
        print(f"  duplicate ted_ids            {dupes}")

        print("\nsample -- what the tender detail screen reads:")
        rows = db.psql(
            "copy (select t.ted_id, r.req_type, r.key, coalesce(r.threshold_num::text, r.threshold_text), r.page "
            "from requirements r join tenders t on t.id = r.tender_id "
            "order by t.ted_id, r.req_type limit 6) to stdout with (format csv)")
        for line in rows.strip().splitlines():
            print("  ", line)

        return 0 if orphans == "0" and dupes == "0" else 1
    finally:
        db.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
