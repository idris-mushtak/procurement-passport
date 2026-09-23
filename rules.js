// supabase/functions/evaluate/rules.ts
var LADDER = {
  1: [
    "Paperwork",
    7
  ],
  2: [
    "Buy or upgrade",
    21
  ],
  3: [
    "Hire or train",
    60
  ],
  4: [
    "Certify",
    150
  ],
  5: [
    "Build a track record",
    null
  ],
  6: [
    "Structurally blocked",
    null
  ]
};
var BID_WRITING_MARGIN_DAYS = 5;
var BASE_LEVEL = {
  certification: 4,
  insurance: 2,
  staff_language: 3,
  turnover: 5,
  references: 5,
  local_presence: 5
};
var ADMIN_ATTESTATIONS = /* @__PURE__ */ new Set([
  "gva",
  "gedragsverklaring",
  "gedragsverklaring_aanbesteden",
  "belastingdienst_verklaring",
  "belastingdienst",
  "handelsregister",
  "handelsregister_uittreksel",
  "kvk_uittreksel",
  "uea",
  "espd",
  "eigen_verklaring",
  "verklaring_belastingdienst",
  "trade_register_registration"
]);
function daysBetween(from, to) {
  return Math.round((to.getTime() - from.getTime()) / 864e5);
}
function parseDate(s) {
  if (!s) return null;
  const d = /* @__PURE__ */ new Date(s.slice(0, 10) + "T00:00:00Z");
  return isNaN(d.getTime()) ? null : d;
}
function capKey(c) {
  return `${c.req_type}::${c.key}`;
}
function countReferences(refs, windowYears, publicRequired, minValue, asOf) {
  return refs.filter((r) => {
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
function checkRequirement(req, company, deadline, asOf) {
  if (req.req_type === "other" || !req.knockout) {
    return {
      status: "review",
      reason: "judgement call -- shown, not scored"
    };
  }
  if (req.req_type === "references") {
    if (req.key === "reference_count") {
      const need = Math.trunc(req.threshold_num ?? 1);
      const have = countReferences(company.references, req.window_years, req.public_sector_required, null, asOf);
      const reason = `${have} qualifying references, ${need} required`;
      return have >= need ? {
        status: "pass",
        reason
      } : {
        status: "gap",
        reason
      };
    }
    return {
      status: "review",
      reason: "reference detail rule needs a human"
    };
  }
  const cap = company.capabilities.find((c) => capKey(c) === `${req.req_type}::${req.key}`);
  if (req.operator === "exists") {
    if (!cap) return {
      status: "gap",
      reason: `no ${req.key} on file`
    };
    const until = parseDate(cap.valid_until);
    if (until && deadline && until < deadline) {
      return {
        status: "gap",
        reason: `${req.key} expires ${cap.valid_until}, before the deadline`
      };
    }
    return {
      status: "pass",
      reason: `${req.key} held (${cap.trust})`
    };
  }
  if (req.operator === "gte" || req.operator === "count_gte") {
    if (!cap || cap.value_num === null) {
      return {
        status: "gap",
        reason: `no ${req.key} on file`
      };
    }
    const have = Number(cap.value_num);
    const need = Number(req.threshold_num ?? 0);
    return have >= need ? {
      status: "pass",
      reason: `${fmt(have)} >= ${fmt(need)}`
    } : {
      status: "gap",
      reason: `${fmt(have)} < ${fmt(need)} required`
    };
  }
  return {
    status: "review",
    reason: `operator ${req.operator} not auto-checked`
  };
}
function fmt(n) {
  return n.toLocaleString("en-US", {
    maximumFractionDigits: 0
  });
}
function levelFor(req) {
  if (req.req_type === "certification" && ADMIN_ATTESTATIONS.has(req.key)) return 1;
  return BASE_LEVEL[req.req_type] ?? 5;
}
function gapLevel(req, tender) {
  const level = levelFor(req);
  if (level === 5 && !tender.joint_bids_allowed && !tender.subcontracting_allowed) {
    return 6;
  }
  return level;
}
function fixableInTime(level, deadline, asOf, prefs) {
  const maxDays = LADDER[level]?.[1] ?? null;
  if (maxDays === null || !deadline) return false;
  if (level === 4 && prefs.allow_certification === false) return false;
  return maxDays <= daysBetween(asOf, deadline) - BID_WRITING_MARGIN_DAYS;
}
function partnerCovers(partner, gaps, tender, asOf) {
  const covered = [];
  const tenderDivisions = new Set((tender.cpv ?? []).map((c) => c.slice(0, 2)));
  for (const g of gaps) {
    if (g.req_type === "certification" || g.req_type === "staff_language" || g.req_type === "local_presence") {
      if (!tender.subcontracting_allowed) continue;
      const held = partner.capabilities.some((c) => c.req_type === g.req_type && c.key === g.key);
      if (held) covered.push(g.key);
    } else if (g.req_type === "references" || g.req_type === "turnover") {
      if (!tender.joint_bids_allowed) continue;
      const relevant = partner.references.filter((r) => r.public_sector && (tenderDivisions.size === 0 || tenderDivisions.has((r.cpv ?? "").slice(0, 2))));
      const need = g.req_type === "references" ? Math.trunc(g.threshold_num ?? 1) : 1;
      if (relevant.length >= need) covered.push(g.key);
    }
  }
  return covered;
}
function scorePartner(partner, covered, gaps, tender) {
  const coverage = gaps.length ? covered.length / gaps.length : 0;
  const evidence = [];
  let trustSum = 0, trustN = 0;
  for (const key of covered) {
    const cap = partner.capabilities.find((c) => c.key === key);
    if (cap) {
      trustSum += cap.trust === "official" || cap.trust === "verified" ? 1 : 0.5;
      trustN += 1;
      evidence.push({
        fact: cap.value_text ?? cap.key,
        trust: cap.trust,
        source_url: cap.source_url
      });
    }
  }
  for (const r of partner.references.slice(0, 3)) {
    evidence.push({
      fact: `${r.buyer}${r.value_eur ? ` -- EUR ${fmt(r.value_eur)}` : ""}`,
      trust: r.trust,
      source_url: r.source_url
    });
    trustSum += 1;
    trustN += 1;
  }
  const evidenceScore = trustN ? trustSum / trustN : 0.5;
  const tenderDivisions = new Set((tender.cpv ?? []).map((c) => c.slice(0, 2)));
  const relevance = partner.references.some((r) => r.public_sector && tenderDivisions.has((r.cpv ?? "").slice(0, 2))) ? 1 : 0.5;
  const largest = partner.references.reduce((m, r) => Math.max(m, r.value_eur ?? 0), 0);
  const tv = tender.value_eur ?? 0;
  const sizeFit = tv > 0 && largest >= 0.5 * tv && largest <= 3 * tv ? 1 : 0.5;
  const score = 0.5 * coverage + 0.25 * evidenceScore + 0.15 * relevance + 0.1 * sizeFit;
  return {
    score: Math.round(score * 1e3) / 1e3,
    evidence
  };
}
function partnerRole(covered, gaps) {
  const types = new Set(gaps.filter((g) => covered.includes(g.key)).map((g) => g.req_type));
  if (types.has("references") || types.has("turnover")) {
    return types.size > 1 ? "consortium partner" : "capacity provider";
  }
  return "subcontractor for that part of the work";
}
function rankPartners(partners, gaps, tender, company, metAlone, total, asOf, top = 3) {
  const prefs = company.prefs ?? {};
  if (prefs.allow_partners === false) return [];
  const excluded = new Set(prefs.excluded_partners ?? []);
  const minValue = prefs.partner_min_contract_value_eur ?? 0;
  const recs = [];
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
      evidence
    });
  }
  recs.sort((a, b) => b.score - a.score || b.gaps_covered.length - a.gaps_covered.length);
  return recs.slice(0, top).map((r, i) => ({
    ...r,
    rank: i + 1
  }));
}
function evaluateTender(tender, requirements, company, partners, asOf) {
  const prefs = company.prefs ?? {};
  const deadline = parseDate(tender.deadline);
  const rows = [];
  const gaps = [];
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
        threshold_num: req.threshold_num
      });
    }
    rows.push({
      requirement: req,
      result
    });
  }
  const total = met + gaps.length;
  const remaining = gaps.filter((g) => !g.fixable_in_time);
  const recs = remaining.length ? rankPartners(partners, remaining, tender, company, met, total, asOf) : [];
  let label;
  if (gaps.some((g) => g.gap_level === 6)) {
    label = "out_of_reach";
  } else if (gaps.length === 0) {
    label = "ready";
  } else if (gaps.every((g) => g.fixable_in_time)) {
    label = "reachable_fix";
  } else {
    const needed = new Set(remaining.map((g) => g.key));
    const closed = recs.some((r) => [
      ...needed
    ].every((k) => r.gaps_covered.includes(k)));
    label = closed ? "reachable_partner" : "future";
  }
  return {
    tender,
    rows,
    met,
    total,
    reviews,
    gaps,
    label,
    recs
  };
}
export {
  ADMIN_ATTESTATIONS,
  BID_WRITING_MARGIN_DAYS,
  LADDER,
  checkRequirement,
  countReferences,
  daysBetween,
  evaluateTender,
  fixableInTime,
  gapLevel,
  levelFor,
  parseDate,
  partnerCovers,
  partnerRole,
  rankPartners,
  scorePartner
};
