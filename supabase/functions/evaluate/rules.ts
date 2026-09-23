/**
 * The gap engine. Pure functions, no I/O, no network, no LLM.
 *
 * Kept separate from index.ts so it can be unit-tested with `deno test` and
 * cross-checked against tools/dry_run_evaluate.py, which is the Python
 * reference this was ported from.
 */

export type ReqType =
  | "turnover" | "certification" | "references"
  | "insurance" | "staff_language" | "local_presence" | "other";

export type Status = "pass" | "gap" | "review";

export type Label =
  | "ready" | "reachable_fix" | "reachable_partner" | "future" | "out_of_reach";

export interface Requirement {
  id: string;
  tender_id: string;
  req_type: ReqType;
  key: string;
  operator: "gte" | "lte" | "eq" | "exists" | "count_gte";
  threshold_num: number | null;
  threshold_text: string | null;
  window_years: number | null;
  public_sector_required: boolean;
  knockout: boolean;
  source_quote: string;
  page: number | null;
  confidence: string;
}

export interface Capability {
  company_id: string;
  req_type: ReqType;
  key: string;
  value_num: number | null;
  value_text: string | null;
  valid_until: string | null;
  trust: "verified" | "official" | "claim";
  source_url: string | null;
}

export interface Reference {
  company_id: string;
  buyer: string;
  public_sector: boolean;
  cpv: string | null;
  value_eur: number | null;
  end_date: string | null;
  trust: string;
  source_url: string | null;
}

export interface Tender {
  id: string;
  ted_id: string;
  title: string;
  buyer: string;
  value_eur: number | null;
  deadline: string | null;
  cpv: string[] | null;
  joint_bids_allowed: boolean;
  subcontracting_allowed: boolean;
}

export interface Prefs {
  allow_certification?: boolean;
  allow_partners?: boolean;
  partner_min_contract_value_eur?: number;
  excluded_partners?: string[];
}

export interface Company {
  id: string;
  name: string;
  prefs: Prefs;
  capabilities: Capability[];
  references: Reference[];
}

export interface CheckResult {
  status: Status;
  reason: string;
  gap_level?: number;
  fixable_in_time?: boolean;
}

/** level -> [label, max_days]; 5 and 6 are never closeable alone. */
export const LADDER: Record<number, [string, number | null]> = {
  1: ["Evidence", 7],
  2: ["Admin", 21],
  3: ["People", 60],
  4: ["Certification", 150],
  5: ["Track record", null],
  6: ["Hard blocker", null],
};

export const BID_WRITING_MARGIN_DAYS = 5;

const BASE_LEVEL: Record<string, number> = {
  certification: 4,
  insurance: 2,
  staff_language: 3,
  turnover: 5,
  references: 5,
  local_presence: 5,
};

/**
 * Not every "certification" is a six-month audit. Dutch tenders ask for a pile
 * of administrative attestations you request and receive in days. Scoring
 * those at level 4 marks tenders unreachable that a week of paperwork opens.
 */
export const ADMIN_ATTESTATIONS = new Set([
  "gva", "gedragsverklaring", "gedragsverklaring_aanbesteden",
  "belastingdienst_verklaring", "belastingdienst", "handelsregister",
  "handelsregister_uittreksel", "kvk_uittreksel", "uea", "espd",
  "eigen_verklaring", "verklaring_belastingdienst", "trade_register_registration",
]);

export function daysBetween(from: Date, to: Date): number {
  return Math.round((to.getTime() - from.getTime()) / 86_400_000);
}

export function parseDate(s: string | null): Date | null {
  if (!s) return null;
  const d = new Date(s.slice(0, 10) + "T00:00:00Z");
  return isNaN(d.getTime()) ? null : d;
}

function capKey(c: Capability): string {
  return `${c.req_type}::${c.key}`;
}

export function countReferences(
  company_references: Reference[],
  windowYears: number | null,
  publicRequired: boolean,
  minValue: number | null,
  asOf: Date,
): number {
  return company_references.filter((r) => {
    if (publicRequired && !r.public_sector) return false;
    if (minValue && (r.value_eur ?? 0) < minValue) return false;
    if (windowYears) {
      const end = parseDate(r.end_date);
      if (!end) return false;
      if (daysBetween(end, asOf) > windowYears * 365) return false;
    }
    return true;
  }).length;
}

/**
 * pass / gap / review for one requirement.
 *
 * Never returns `pass` on a judgement call. That restraint is the product's
 * central claim: the engine's false-eligible rate is zero by construction,
 * because anything it cannot decide it surfaces as `review` instead.
 */
export function checkRequirement(
  req: Requirement,
  company: Company,
  deadline: Date | null,
  asOf: Date,
): CheckResult {
  if (req.req_type === "other" || !req.knockout) {
    return { status: "review", reason: "judgement call -- shown, not scored" };
  }

  if (req.req_type === "references") {
    if (req.key === "reference_count") {
      const need = Math.trunc(req.threshold_num ?? 1);
      const have = countReferences(
        company.references, req.window_years, req.public_sector_required, null, asOf,
      );
      const reason = `${have} qualifying references, ${need} required`;
      return have >= need ? { status: "pass", reason } : { status: "gap", reason };
    }
    return { status: "review", reason: "reference detail rule needs a human" };
  }

  const cap = company.capabilities.find(
    (c) => capKey(c) === `${req.req_type}::${req.key}`,
  );

  if (req.operator === "exists") {
    if (!cap) return { status: "gap", reason: `no ${req.key} on file` };
    const until = parseDate(cap.valid_until);
    if (until && deadline && until < deadline) {
      return {
        status: "gap",
        reason: `${req.key} expires ${cap.valid_until}, before the deadline`,
      };
    }
    return { status: "pass", reason: `${req.key} held (${cap.trust})` };
  }

  if (req.operator === "gte" || req.operator === "count_gte") {
    if (!cap || cap.value_num === null) {
      return { status: "gap", reason: `no ${req.key} on file` };
    }
    const have = Number(cap.value_num);
    const need = Number(req.threshold_num ?? 0);
    return have >= need
      ? { status: "pass", reason: `${fmt(have)} >= ${fmt(need)}` }
      : { status: "gap", reason: `${fmt(have)} < ${fmt(need)} required` };
  }

  return { status: "review", reason: `operator ${req.operator} not auto-checked` };
}

function fmt(n: number): string {
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function levelFor(req: Requirement): number {
  if (req.req_type === "certification" && ADMIN_ATTESTATIONS.has(req.key)) return 1;
  return BASE_LEVEL[req.req_type] ?? 5;
}

/** A level-5 gap on a tender barring both routes cannot be closed at all. */
export function gapLevel(req: Requirement, tender: Tender): number {
  const level = levelFor(req);
  if (level === 5 && !tender.joint_bids_allowed && !tender.subcontracting_allowed) {
    return 6;
  }
  return level;
}

export function fixableInTime(
  level: number, deadline: Date | null, asOf: Date, prefs: Prefs,
): boolean {
  const maxDays = LADDER[level]?.[1] ?? null;
  if (maxDays === null || !deadline) return false;
  // "Allow certification: no" means the company will not pursue a certificate,
  // so a level-4 gap stops being fixable even when the calendar allows it.
  if (level === 4 && prefs.allow_certification === false) return false;
  return maxDays <= daysBetween(asOf, deadline) - BID_WRITING_MARGIN_DAYS;
}

// ---------------------------------------------------------------- partners

export interface Gap {
  requirement_id: string;
  req_type: ReqType;
  key: string;
  gap_level: number;
  label: string;
  fixable_in_time: boolean;
  reason: string;
  threshold_num: number | null;
}

export interface Partner extends Company {
  awards: Reference[];
}

export interface EvidenceItem {
  fact: string;
  trust: string;
  source_url: string | null;
}

export interface PartnerRec {
  partner_id: string;
  partner_name: string;
  rank: number;
  score: number;
  gaps_covered: string[];
  joint_met: number;
  joint_total: number;
  role: string;
  evidence: EvidenceItem[];
}

/**
 * Which of these gaps a partner can close ON THIS TENDER.
 *
 * Two constraints that are easy to drop and change every answer:
 *   - capacity (references, turnover) can only be borrowed in a combination,
 *     so `joint_bids_allowed` must hold;
 *   - capability (certification, staff, presence) means putting the partner on
 *     the work, so `subcontracting_allowed` must hold.
 * Without both, every tender comes back reachable -- the one answer a
 * procurement tool must never give by default.
 */
export function partnerCovers(
  partner: Partner, gaps: Gap[], tender: Tender, asOf: Date,
): string[] {
  const covered: string[] = [];
  const tenderDivisions = new Set((tender.cpv ?? []).map((c) => c.slice(0, 2)));

  for (const g of gaps) {
    if (g.req_type === "certification" || g.req_type === "staff_language" ||
        g.req_type === "local_presence") {
      if (!tender.subcontracting_allowed) continue;
      const held = partner.capabilities.some(
        (c) => c.req_type === g.req_type && c.key === g.key,
      );
      if (held) covered.push(g.key);
    } else if (g.req_type === "references" || g.req_type === "turnover") {
      if (!tender.joint_bids_allowed) continue;
      // A reference only counts if it is the same kind of work. CPV division
      // is coarse, but it is what award data can actually support.
      const relevant = partner.references.filter(
        (r) =>
          r.public_sector &&
          (tenderDivisions.size === 0 ||
            tenderDivisions.has((r.cpv ?? "").slice(0, 2))),
      );
      const need = g.req_type === "references"
        ? Math.trunc(g.threshold_num ?? 1)
        : 1;
      if (relevant.length >= need) covered.push(g.key);
    }
  }
  return covered;
}

/** score = 0.5 coverage + 0.25 evidence + 0.15 relevance + 0.10 size_fit */
export function scorePartner(
  partner: Partner, covered: string[], gaps: Gap[], tender: Tender,
): { score: number; evidence: EvidenceItem[] } {
  const coverage = gaps.length ? covered.length / gaps.length : 0;

  const evidence: EvidenceItem[] = [];
  let trustSum = 0, trustN = 0;
  for (const key of covered) {
    const cap = partner.capabilities.find((c) => c.key === key);
    if (cap) {
      trustSum += cap.trust === "official" || cap.trust === "verified" ? 1.0 : 0.5;
      trustN += 1;
      evidence.push({
        fact: cap.value_text ?? cap.key,
        trust: cap.trust,
        source_url: cap.source_url,
      });
    }
  }
  for (const r of partner.references.slice(0, 3)) {
    evidence.push({
      fact: `${r.buyer}${r.value_eur ? ` -- EUR ${fmt(r.value_eur)}` : ""}`,
      trust: r.trust,
      source_url: r.source_url,
    });
    trustSum += 1.0;   // award notices are published records
    trustN += 1;
  }
  const evidenceScore = trustN ? trustSum / trustN : 0.5;

  const tenderDivisions = new Set((tender.cpv ?? []).map((c) => c.slice(0, 2)));
  const relevance = partner.references.some(
    (r) => r.public_sector && tenderDivisions.has((r.cpv ?? "").slice(0, 2)),
  ) ? 1.0 : 0.5;

  const largest = partner.references.reduce(
    (m, r) => Math.max(m, r.value_eur ?? 0), 0,
  );
  const tv = tender.value_eur ?? 0;
  const sizeFit = tv > 0 && largest >= 0.5 * tv && largest <= 3 * tv ? 1.0 : 0.5;

  const score = 0.5 * coverage + 0.25 * evidenceScore +
                0.15 * relevance + 0.10 * sizeFit;
  return { score: Math.round(score * 1000) / 1000, evidence };
}

export function partnerRole(covered: string[], gaps: Gap[]): string {
  const types = new Set(
    gaps.filter((g) => covered.includes(g.key)).map((g) => g.req_type),
  );
  if (types.has("references") || types.has("turnover")) {
    return types.size > 1 ? "consortium partner" : "capacity provider";
  }
  return "subcontractor for that part of the work";
}

export function rankPartners(
  partners: Partner[], gaps: Gap[], tender: Tender, company: Company,
  metAlone: number, total: number, asOf: Date, top = 3,
): PartnerRec[] {
  const prefs = company.prefs ?? {};
  if (prefs.allow_partners === false) return [];
  const excluded = new Set(prefs.excluded_partners ?? []);
  const minValue = prefs.partner_min_contract_value_eur ?? 0;

  const recs: PartnerRec[] = [];
  for (const p of partners) {
    if (excluded.has(p.id)) continue;
    if (minValue > 0) {
      const largest = p.references.reduce((m, r) => Math.max(m, r.value_eur ?? 0), 0);
      if (largest < minValue) continue;
    }
    const covered = partnerCovers(p, gaps, tender, asOf);
    if (covered.length === 0) continue;
    const { score, evidence } = scorePartner(p, covered, gaps, tender);
    recs.push({
      partner_id: p.id,
      partner_name: p.name,
      rank: 0,
      score,
      gaps_covered: covered,
      // "you + Company B: 19 of 19" is computed by re-running the check with
      // the partner merged in, not asserted.
      joint_met: metAlone + covered.length,
      joint_total: total,
      role: partnerRole(covered, gaps),
      evidence,
    });
  }
  recs.sort((a, b) =>
    b.score - a.score || b.gaps_covered.length - a.gaps_covered.length
  );
  return recs.slice(0, top).map((r, i) => ({ ...r, rank: i + 1 }));
}

// ------------------------------------------------------------------ labels

export interface TenderOutcome {
  tender: Tender;
  rows: Array<{ requirement: Requirement; result: CheckResult }>;
  met: number;
  total: number;
  reviews: number;
  gaps: Gap[];
  label: Label;
  recs: PartnerRec[];
}

export function evaluateTender(
  tender: Tender,
  requirements: Requirement[],
  company: Company,
  partners: Partner[],
  asOf: Date,
): TenderOutcome {
  const prefs = company.prefs ?? {};
  const deadline = parseDate(tender.deadline);
  const rows: TenderOutcome["rows"] = [];
  const gaps: Gap[] = [];
  let met = 0, reviews = 0;

  for (const req of requirements) {
    const result = checkRequirement(req, company, deadline, asOf);
    if (result.status === "pass") met += 1;
    else if (result.status === "review") reviews += 1;
    else {
      const level = gapLevel(req, tender);
      const fixable = fixableInTime(level, deadline, asOf, prefs);
      result.gap_level = level;
      result.fixable_in_time = fixable;
      gaps.push({
        requirement_id: req.id,
        req_type: req.req_type,
        key: req.key,
        gap_level: level,
        label: LADDER[level][0],
        fixable_in_time: fixable,
        reason: result.reason,
        threshold_num: req.threshold_num,
      });
    }
    rows.push({ requirement: req, result });
  }

  const total = met + gaps.length;
  const remaining = gaps.filter((g) => !g.fixable_in_time);
  const recs = remaining.length
    ? rankPartners(partners, remaining, tender, company, met, total, asOf)
    : [];

  // First match wins, per the design doc's label table.
  let label: Label;
  if (gaps.some((g) => g.gap_level === 6)) {
    label = "out_of_reach";
  } else if (gaps.length === 0) {
    label = "ready";
  } else if (gaps.every((g) => g.fixable_in_time)) {
    label = "reachable_fix";
  } else {
    const needed = new Set(remaining.map((g) => g.key));
    const closed = recs.some((r) =>
      [...needed].every((k) => r.gaps_covered.includes(k))
    );
    label = closed ? "reachable_partner" : "future";
  }

  return { tender, rows, met, total, reviews, gaps, label, recs };
}
