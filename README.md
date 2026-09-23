# Procurement Passport — data pipeline (Person A)

Offline prep for the 3-hour build: turns live TED and TenderNed data into the
SQL seeds that Supabase loads and the Lovable app reads. The LLM runs only
here, before the demo. Nothing in the live demo waits on a model.

**Scope of this repo:** Person A's column of the build plan — tender curation,
requirement extraction, partner seed, demo company — plus the schema contract
those seeds target. Person B owns the Supabase project and the `/evaluate`
Edge Function; Person C owns the Lovable frontend.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # NEBIUS_API_KEY optional, see below

python -m prep.run all        # select → packs → extract → partners → facts → emit
python -m prep.run report     # the per-tender spot check, one screen
python tools/dry_run_evaluate.py   # what /evaluate should produce on this data
```

## Handing this to whoever owns the Supabase project

Send them **`out/00_bundle.sql`** and this instruction:

> Open the Supabase SQL editor, paste the whole file, run it. Safe to re-run.

That one file is schema + ladder + demo company + 12 tenders + 110
requirements + 30 partners + RLS, already in load order — there is no way to
paste it in the wrong sequence. Regenerate it with `python tools/make_bundle.py`
after any change.

If they prefer the individual files, the order is:

```
sql/01_schema.sql   sql/02_ladder.sql   sql/03_demo_company.sql
out/04_tenders.sql  out/05_requirements.sql  out/06_partners.sql
sql/07_rls.sql
```

Two things to tell them explicitly:

- **the table is `refs`, not `references`** — reserved word in Postgres;
- **`07_rls.sql` is Supabase-specific.** Without it every table is readable
  *and writable* by anyone holding the anon key. It grants public read only;
  the service role bypasses RLS, so `prep.py` and `/evaluate` are unaffected.
  Verified locally: anon reads 12 tenders, anon `delete` is denied.

## Test it

```bash
./tools/check.sh          # all four checks, ~40s, no Supabase needed
```

| Check | What it proves |
| --- | --- |
| `tools/validate_sql.py` | all six files parse under Postgres' own grammar (pglast) |
| `tools/verify_quotes.py` | every `source_quote` really occurs in the pack it cites |
| `tools/load_test.py` | the seeds **execute** in a throwaway Postgres: constraints hold, FKs resolve, `gap_summary` compiles, no orphans |
| `tools/dry_run_evaluate.py` | the data produces the five demo screens |

`load_test.py` uses `pgserver`, which bundles real Postgres binaries — no
Docker, no Supabase project, nothing to configure. It loads with
`ON_ERROR_STOP` so a broken seed fails the run instead of quietly dropping
rows.

## What the data actually is

Everything is live, fetched during this build, and cited:

| Layer | Source | Trust |
| --- | --- | --- |
| Tender metadata | TED eForms UBL XML, `ted.europa.eu/en/notice/<id>/xml` | official |
| Requirements | TenderNed document packs via its public JSON API | extracted, clause-cited |
| Partner references | TED award notices (1 255 NL IT awards since 2024) | official |
| Partner certificates | the partner's own website, read and quoted | claim |
| Demo company | fictional, `data/curated/demo_company.json` | verified (seeded) |

Two findings worth knowing, both checked against all 95 notices in the CSV:

- **Selection criteria are almost never in the TED notice.** 2 of 95 publish
  them; 46 point at the procurement documents and 76 at the ESPD. So the
  pipeline reads the document pack — the notice alone cannot answer the
  question the product asks.
- **TenderNed's document API is open.** `/papi/tenderned-rs-tns/v2/publicaties/
  <id>/documenten` lists every published file with a direct download link, no
  auth. Mercell is not: `api.s2c.mercell.com` is live but answers `403
  Forbidden` without a session, and the EU portal exposes no equivalent JSON.
  So TenderNed is the only portal this pipeline can extract from today.

### What that costs us

Pack availability is a scoring bonus in `select_tenders.py`, not a hard filter,
but with only 12 slots it became one — all 12 picks are TenderNed. Measured
against the 88 eligible tenders in the CSV:

| Portal | Tenders | Combined value |
| --- | ---: | ---: |
| TenderNed | 62 | €120M |
| Mercell | 21 | €192M |
| EU portal | 3 | €714M |
| other | 2 | €2.8M |

So the demo set is **representative by tender count** — TenderNed is 70% of the
open NL IT market — and **unrepresentative by value**, because the largest
contracts sit on the EU portal. Re-ranking with the pack bonus removed changes
only 3 of the 12 picks, so fetchability was not the dominant selection factor;
deadline runway, known value and SME-plausible size were. The two tenders it
displaces are a €2.8M fibre contract and a €2 qualification-system placeholder.

The honest framing for the demo: this covers the portal where 7 in 10 Dutch IT
tenders are published. Mercell needs a logged-in session to extend it, and that
is a real piece of roadmap, not a detail.

## How extraction works

Two backends, merged (`--backend both`, the default):

- **`rules`** (`prep/rules_extract.py`) — deterministic Dutch patterns. Dutch
  leidraden state hard thresholds in stereotyped forms, and a pattern that
  matches those is right or silent, never confidently wrong. This is what
  reads `€ 1.250.000 per gebeurtenis` correctly. Runs with no API key.
- **`llm`** (`prep/extract_requirements.py`) — Nebius, `DeepSeek-V4.1-Flash`
  with a single escalation to `DeepSeek-V4-Pro` when a response fails Pydantic
  validation. Catches prose requirements the patterns cannot reach: ISO/IEC
  20000-1, kerncompetentie sub-thresholds, Gedragsverklaring Aanbesteden.
  Calls are bounded at 150s and cached per prompt hash, so a re-run after a
  rules or override change costs nothing for tenders that did not move. A
  timeout on one tender keeps that tender's pattern rows and moves on.

On a merge the pattern wins for numbers (it read the digits) and the model wins
for coverage. Every row carries `source_quote` and `page`; a row that fails
validation is dropped, never patched.

**Hand corrections** go in `data/curated/overrides.json` — `drop` a wrong row,
`set` fields on a nearly-right one, `add` a clause a human read. The design
doc's "spot-check every tender by hand" is a 3-minute job per tender against
`python -m prep.run report`.

## Honest state of the output

| | |
| --- | --- |
| Tenders | 12, all open, €70.9M, deadlines 28–107 days out |
| Requirements | 110 extracted (rules + DeepSeek-V4.1-Flash merged), every one clause-cited |
| Citation check | 88% verbatim in the pack, 12% near-match, **0 unciteable** |
| Partners | 30 real firms, 292 award-backed public references |
| Partner certificates | 18 facts across 4 firms (Afas, Centric, PQR, ilionx), each quoted from their site |

Three things are thinner than the design doc assumes, and none of them are
hidden by the demo:

1. **Coverage is 2–16 requirements per tender** (mean 9), up from 1–5 on the
   patterns alone. `tools/verify_quotes.py` checks every `source_quote` against
   the document pack it came from; on the shipped data 97 are verbatim, 13 are
   near-matches after PDF hyphenation noise, and none are unfindable. Run it
   after any extraction change — it is the anti-hallucination gate, and it
   already caught one fabricated clause (a hand-written override that
   paraphrased instead of quoting).
2. **Partner certificates cover 4 of 30 firms** (all four hold ISO 27001, the
   gap the demo turns on). The scanner records a certification only where it
   read a possession statement on the company's own page; most of these sites
   render theirs in JavaScript, so the count varies by a firm or two between
   runs and the misses are listed as needing a human rather than guessed —
   inventing an ISO claim about a named real company is the one output this
   project must never produce. `data/curated/partner_facts.csv` and
   `partner_homepages.json` make hand-entry a ten-minute job.
3. **`refs`, not `references`.** `references` is a reserved word in Postgres.
   The table in `sql/01_schema.sql` is `refs`; tell Lovable that name.

## Reference engine

`tools/dry_run_evaluate.py` is a Python implementation of the gap engine —
**not** the production one, which is Person B's TypeScript Edge Function. It
exists so the demo company can be tuned against real verdicts, and so the TS
port has executable behaviour to match instead of a table in a doc.

On the current data it produces:

```
10 of 12 tenders within reach, worth EUR 34,931,500
ISO 27001 blocks 10 tenders (EUR 63.1M); unlocks 4 alone (EUR 13.2M)
PPM (Spaarnelanden) — you alone 2/3, you + Afas Software 3/3
2 tenders out of reach on capability the demo company cannot borrow
```

Three rules it enforces that are easy to drop in a port, and which are each the
difference between a real answer and a wrong one:

- capacity gaps (references, turnover) can only be borrowed when
  `joint_bids_allowed`;
- capability gaps (certification, staff, presence) need `subcontracting_allowed`.
  Without these two, every tender comes back reachable;
- **not every `certification` is a six-month audit.** Dutch tenders ask for a
  Gedragsverklaring Aanbesteden, a Belastingdienst payment statement, a
  Handelsregister extract — days of paperwork, ladder level 1. Scoring those at
  level 4 marked two genuinely reachable tenders as out of reach. See
  `ADMIN_ATTESTATIONS` in the reference engine.

One judgement call left open for Person B: a clause reading *"een
kwaliteitszorgsysteem dat minimaal gelijkwaardig is aan een gecertificeerd
systeem"* names no certificate, so it is a `review` item, not a gap — while
*"ISO 27001 of gelijkwaardig"* names one and stays checkable. The extractor
currently classifies both as `certification`; only the first is wrong.

## Layout

```
prep/
  config.py              paths, vocabulary, model names, keyword weights
  models.py              Pydantic contracts, mirroring the SQL tables
  fetch_notices.py       TED notice XML for all 95 CSV rows
  notice_parser.py       eForms UBL → tender metadata, exclusion grounds
  select_tenders.py      scores and picks the 12, max 2 per buyer
  tenderned.py           public API → ranked document pack
  docs.py                PDF/DOCX → the pages that state requirements
  rules_extract.py       deterministic Dutch threshold patterns
  llm.py                 Nebius client, JSON recovery, smoke test
  extract_requirements.py  llm/rules/both + overrides
  partners.py            TED award search → ranked partner pool
  partner_facts.py       reads partner sites for evidenced certifications
  emit_sql.py            SQL writers
  run.py                 CLI
tools/
  validate_sql.py        parse every .sql with Postgres' grammar
  verify_quotes.py       every source_quote must exist in the pack
  dry_run_evaluate.py    reference gap engine
```

## Secrets

`.env` holds the Nebius key and is gitignored. It is currently populated with
the key you supplied — rotate it if this tree is ever pushed anywhere.
