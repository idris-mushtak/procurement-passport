#!/usr/bin/env bash
# Everything that can be verified without Supabase or a frontend.
# Run from the repo root: ./tools/check.sh
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
fail=0

step () {
  echo
  echo "=============================================================="
  echo "  $1"
  echo "=============================================================="
  shift
  "$@" || { echo ">>> FAILED: $*"; fail=1; }
}

step "1/6  SQL parses under Postgres' own grammar"  python3 tools/validate_sql.py
step "2/6  every source_quote exists in the document pack" python3 tools/verify_quotes.py
step "3/6  all six seed files load into a real Postgres"  python3 tools/load_test.py
step "4/6  the Edge Function matches the Python engine"  bash -c 'PATH="$HOME/.deno/bin:$PATH" deno test --allow-read supabase/functions/evaluate/rules_test.ts'
step "5/6  the gap engine produces a demo"  python3 tools/dry_run_evaluate.py
step "6/6  LLM cascade logic (mocked, no network)"  python3 -m pytest tests/ -q

echo
if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; fi
exit "$fail"
