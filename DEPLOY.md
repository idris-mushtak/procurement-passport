# Deploying `/evaluate`

The Edge Function lives in `supabase/functions/evaluate/`:

```
index.ts       HTTP handler: loads the working set, writes results back
rules.ts       the gap engine, pure functions, no I/O
rules_test.ts  12 tests, including agreement with the Python reference
```

## Deploy

```bash
npx supabase link --project-ref <your-project-ref>
npx supabase functions deploy evaluate
```

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are injected automatically. The
service role is required — RLS grants the anon key read-only access, and this
function writes.

## Call it

```bash
curl -X POST "https://<ref>.supabase.co/functions/v1/evaluate" \
  -H "Authorization: Bearer <ANON_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"company_id":"11111111-1111-1111-1111-111111111111"}'
```

Returns:

```json
{"ok": true, "company": "Meridiaan Digitaal B.V.", "tenders": 12,
 "results": 108, "partner_recs": 21,
 "labels": {"reachable_partner": 8, "reachable_fix": 2, "future": 2}, "ms": 380}
```

Call it on app load and after every `companies.prefs` update. It clears and
rewrites `results`, `tender_status` and `partner_recs` for that company only.

## Test before deploying

```bash
deno test --allow-read supabase/functions/evaluate/rules_test.ts
```

The last test replays the real seed data through the TypeScript engine and
asserts it produces the same label, met and total for all 12 tenders as
`tools/dry_run_evaluate.py`. If the port drifts, that test fails.

## Three rules the port must keep

1. Capacity gaps (references, turnover) can only be borrowed when
   `joint_bids_allowed`; capability gaps (certification, staff, presence) need
   `subcontracting_allowed`. Drop these and every tender comes back reachable.
2. Administrative attestations — `gva`, `belastingdienst_verklaring`,
   `handelsregister_uittreksel` — are ladder 1, not 4. See `ADMIN_ATTESTATIONS`.
3. `req_type = "other"` never returns `pass`. It returns `review`. That is the
   zero-false-eligible claim, and it is load-bearing.
