# Backend — live

Project **procurement-passport**, ref `acdnnqeyygjdupuznnmz`, region eu-central-1.

```
SUPABASE_URL       https://acdnnqeyygjdupuznnmz.supabase.co
SUPABASE_ANON_KEY  see .env (gitignored)
```

Everything below is deployed and verified against the live project, not just
locally. `tender_status` in Supabase matches `tools/dry_run_evaluate.py`
tender-for-tender.

## What's in it

| | |
| --- | --- |
| tenders | 12 (6 with combinatie or onderaanneming barred) |
| requirements | 108, every one with a verbatim clause and page number |
| companies | 31 (30 TED-award partners + 1 demo) |
| company_references | 295, all from published award notices |
| capabilities | 24 (6 demo verified, 18 partner claims) |
| results / tender_status / partner_recs | 108 / 12 / 24, written by `/evaluate` |
| RLS | 11 tables, 11 read policies, no public write |

## `/evaluate`

```bash
curl -X POST "$SUPABASE_URL/functions/v1/evaluate" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"company_id":"11111111-1111-1111-1111-111111111111"}'
```

Live response: 12 tenders, 108 results, 24 partner recs, 634 ms.
Labels: 7 reachable_partner, 2 reachable_fix, 3 future.

Call it on load and after every preference change.

## Preferences from the browser

RLS grants the anon key read-only. The one write the frontend needs goes
through a function with a key whitelist, so a payload containing `name` or
`is_demo` is ignored rather than stored:

```js
await supabase.rpc('update_prefs', {
  p_company_id: '11111111-1111-1111-1111-111111111111',
  p_prefs: { allow_partners: false }
});
// then re-call /evaluate
```

## Security advisor

The `security_definer_view` ERROR on `gap_summary` and `cost_summary` is fixed
(`security_invoker = true`). Two WARNs remain, both for `update_prefs` being
callable by anon and authenticated — that is the intended design, not a defect.

## Still needs a human

- **`SUPABASE_SERVICE_ROLE_KEY`** is not in `.env`. The LLM cost ledger falls
  back to `prep/.llm_calls.jsonl` instead of writing `llm_calls`, so
  `cost_summary` stays empty until you paste the key from
  Settings → API → service_role.
- **TODO prices** in `prep/llm/config.py` for `DeepSeek-V4-Pro` and
  `Qwen3.5-397B-A17B` — both 0.0, so SMART-tier cost currently reads as zero.
- **TryNobu is paused** to free the free-tier slot. Restore it from the
  dashboard, or upgrade the org, when you need it back.
