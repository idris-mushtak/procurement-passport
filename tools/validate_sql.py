"""Parse every emitted SQL file with Postgres's own grammar (libpg_query via
pglast). Catches the failure that actually bites during a hackathon: a Dutch
clause with an apostrophe, or a reserved word used as a table name, breaking
the seed load at minute 90."""
import sys
from pathlib import Path

import pglast

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted(ROOT.glob("sql/*.sql")) + sorted(ROOT.glob("out/*.sql"))

rc = 0
for f in FILES:
    sql = f.read_text(encoding="utf-8")
    try:
        stmts = pglast.parse_sql(sql)
        print(f"  OK    {f.relative_to(ROOT)}  ({len(stmts)} statements)")
    except pglast.parser.ParseError as exc:
        rc = 1
        line = sql[:exc.location].count("\n") + 1 if exc.location else "?"
        print(f"  FAIL  {f.relative_to(ROOT)}  line {line}: {exc}")
sys.exit(rc)
