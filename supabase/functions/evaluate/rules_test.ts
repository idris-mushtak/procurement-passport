/**
 * Unit tests plus a cross-language check against the Python reference engine.
 *
 *   deno test --allow-read supabase/functions/evaluate/rules_test.ts
 *
 * The cross-check is the important one: tools/dry_run_evaluate.py and this
 * port must agree tender-for-tender on identical input, or one of them is
 * wrong and the demo is showing a number nobody can defend.
 */
import { assertEquals } from "jsr:@std/assert@1";
import {
  Company, Partner, Requirement, Tender,
  checkRequirement, evaluateTender, fixableInTime, gapLevel, levelFor, parseDate,
} from "./rules.ts";

const ASOF = new Date("2026-09-23T00:00:00Z");

function req(p: Partial<Requirement>): Requirement {
  return {
    id: "r1", tender_id: "t1", req_type: "certification", key: "iso_27001",
    operator: "exists", threshold_num: null, threshold_text: null,
    window_years: null, public_sector_required: false, knockout: true,
    source_quote: "x".repeat(40), page: 1, confidence: "high", ...p,
  } as Requirement;
}

function tender(p: Partial<Tender> = {}): Tender {
  return {
    id: "t1", ted_id: "1-2026", title: "T", buyer: "B", value_eur: 1_000_000,
    deadline: "2026-12-01", cpv: ["72000000"],
    joint_bids_allowed: true, subcontracting_allowed: true, ...p,
  };
}

const company: Company = {
  id: "c1", name: "Demo", prefs: {},
  capabilities: [
    { company_id: "c1", req_type: "certification", key: "iso_9001",
      value_num: null, value_text: "ISO 9001", valid_until: "2027-06-30",
      trust: "verified", source_url: null },
    { company_id: "c1", req_type: "insurance", key: "liability_insurance_eur",
      value_num: 1_000_000, value_text: null, valid_until: null,
      trust: "verified", source_url: null },
  ],
  references: [
    { company_id: "c1", buyer: "Gemeente A", public_sector: true, cpv: "72212000",
      value_eur: 240_000, end_date: "2025-04-30", trust: "verified", source_url: null },
    { company_id: "c1", buyer: "Private B", public_sector: false, cpv: "72212000",
      value_eur: 180_000, end_date: "2024-11-30", trust: "verified", source_url: null },
  ],
};

Deno.test("missing certification is a gap", () => {
  const r = checkRequirement(req({}), company, parseDate("2026-12-01"), ASOF);
  assertEquals(r.status, "gap");
});

Deno.test("held certification passes", () => {
  const r = checkRequirement(req({ key: "iso_9001" }), company,
                             parseDate("2026-12-01"), ASOF);
  assertEquals(r.status, "pass");
});

Deno.test("certificate expiring before the deadline is a gap", () => {
  const r = checkRequirement(req({ key: "iso_9001" }), company,
                             parseDate("2027-12-01"), ASOF);
  assertEquals(r.status, "gap");
});

Deno.test("insurance below threshold is a gap, at threshold passes", () => {
  const below = checkRequirement(
    req({ req_type: "insurance", key: "liability_insurance_eur",
          operator: "gte", threshold_num: 1_250_000 }),
    company, parseDate("2026-12-01"), ASOF);
  assertEquals(below.status, "gap");
  const at = checkRequirement(
    req({ req_type: "insurance", key: "liability_insurance_eur",
          operator: "gte", threshold_num: 1_000_000 }),
    company, parseDate("2026-12-01"), ASOF);
  assertEquals(at.status, "pass");
});

Deno.test("public-sector-only reference requirement ignores private work", () => {
  const r = checkRequirement(
    req({ req_type: "references", key: "reference_count", operator: "count_gte",
          threshold_num: 2, public_sector_required: true }),
    company, parseDate("2026-12-01"), ASOF);
  assertEquals(r.status, "gap");   // only one public reference on file
});

Deno.test("judgement calls are review, never pass", () => {
  const r = checkRequirement(req({ req_type: "other" }), company,
                             parseDate("2026-12-01"), ASOF);
  assertEquals(r.status, "review");
});

Deno.test("admin attestations are ladder 1, real certifications ladder 4", () => {
  assertEquals(levelFor(req({ key: "gva" })), 1);
  assertEquals(levelFor(req({ key: "iso_27001" })), 4);
});

Deno.test("level 5 becomes level 6 when both routes are barred", () => {
  const r = req({ req_type: "references", key: "reference_count" });
  assertEquals(gapLevel(r, tender()), 5);
  assertEquals(
    gapLevel(r, tender({ joint_bids_allowed: false, subcontracting_allowed: false })),
    6,
  );
});

Deno.test("fixable_in_time respects the 5-day bid-writing margin", () => {
  // level 2 needs 21 days + 5 margin = 26
  assertEquals(fixableInTime(2, parseDate("2026-10-20"), ASOF, {}), true);  // 27d
  assertEquals(fixableInTime(2, parseDate("2026-10-18"), ASOF, {}), false); // 25d
});

Deno.test("allow_certification:false makes a level-4 gap unfixable", () => {
  assertEquals(fixableInTime(4, parseDate("2027-06-01"), ASOF, {}), true);
  assertEquals(
    fixableInTime(4, parseDate("2027-06-01"), ASOF, { allow_certification: false }),
    false,
  );
});

Deno.test("allow_partners:false suppresses recommendations", () => {
  const partner: Partner = {
    id: "p1", name: "P", prefs: {}, awards: [],
    capabilities: [{ company_id: "p1", req_type: "certification", key: "iso_27001",
      value_num: null, value_text: "ISO 27001", valid_until: null,
      trust: "claim", source_url: null }],
    references: [],
  };
  const reqs = [req({ id: "x", key: "iso_27001" })];
  const withP = evaluateTender(tender(), reqs, company, [partner], ASOF);
  assertEquals(withP.recs.length, 1);
  const noP = evaluateTender(
    tender(), reqs, { ...company, prefs: { allow_partners: false } }, [partner], ASOF);
  assertEquals(noP.recs.length, 0);
  assertEquals(noP.label, "future");
});

// ------------------------------------------------- cross-language agreement

Deno.test("matches the Python reference engine on the real seed data", async () => {
  const fixture = JSON.parse(await Deno.readTextFile("data/fixture.json"));
  const expected = JSON.parse(await Deno.readTextFile("data/expected_labels.json"));
  const asOf = new Date(fixture.as_of + "T00:00:00Z");

  const byTender = new Map<string, Requirement[]>();
  for (const r of fixture.requirements as Requirement[]) {
    if (!byTender.has(r.tender_id)) byTender.set(r.tender_id, []);
    byTender.get(r.tender_id)!.push(r);
  }

  const got: Record<string, { label: string; met: number; total: number }> = {};
  for (const t of fixture.tenders as Tender[]) {
    const out = evaluateTender(
      t, byTender.get(t.id) ?? [], fixture.company, fixture.partners, asOf);
    got[t.ted_id] = { label: out.label, met: out.met, total: out.total };
  }
  assertEquals(got, expected);
});
