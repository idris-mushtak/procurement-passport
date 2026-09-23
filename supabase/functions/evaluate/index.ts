/**
 * POST /evaluate  { "company_id": "<uuid>" }
 *
 * Recomputes `results`, `tender_status` and `partner_recs` for one company.
 * Called on app load and on every preference change, so it has to be fast:
 * it reads the whole working set in six queries, evaluates in memory, and
 * writes back in three upserts. No LLM, no network beyond Postgres.
 */
import { createClient, SupabaseClient } from "jsr:@supabase/supabase-js@2";
import {
  Capability, Company, Partner, Reference, Requirement, Tender,
  evaluateTender,
} from "./rules.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

/** PostgREST caps a request at 1000 rows; the partner reference table is
 *  bigger than that, so page rather than silently truncating the evidence. */
async function selectAll<T>(
  db: SupabaseClient, table: string, columns = "*",
): Promise<T[]> {
  const out: T[] = [];
  const size = 1000;
  for (let from = 0; ; from += size) {
    const { data, error } = await db
      .from(table).select(columns).range(from, from + size - 1);
    if (error) throw new Error(`${table}: ${error.message}`);
    const rows = (data ?? []) as T[];
    out.push(...rows);
    if (rows.length < size) break;
  }
  return out;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);

  const started = Date.now();
  try {
    const body = await req.json().catch(() => ({}));
    const companyId: string | undefined = body.company_id;
    if (!companyId) return json({ error: "company_id is required" }, 400);

    // The service role key is required: this function writes to tables whose
    // RLS grants the anon key read-only access.
    const db = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
      { auth: { persistSession: false } },
    );

    const asOf = body.as_of ? new Date(body.as_of) : new Date();

    const [companies, capabilities, company_references, tenders, requirements] =
      await Promise.all([
        selectAll<Record<string, unknown>>(db, "companies"),
        selectAll<Capability>(db, "capabilities"),
        selectAll<Reference>(db, "company_references"),
        selectAll<Tender>(db, "tenders"),
        selectAll<Requirement>(db, "requirements"),
      ]);

    const companyRow = companies.find((c) => c.id === companyId);
    if (!companyRow) return json({ error: `company ${companyId} not found` }, 404);

    const capsBy = new Map<string, Capability[]>();
    for (const c of capabilities) {
      (capsBy.get(c.company_id) ?? capsBy.set(c.company_id, []).get(c.company_id)!)
        .push(c);
    }
    const refsBy = new Map<string, Reference[]>();
    for (const r of company_references) {
      (refsBy.get(r.company_id) ?? refsBy.set(r.company_id, []).get(r.company_id)!)
        .push(r);
    }

    const company: Company = {
      id: companyId,
      name: String(companyRow.name ?? ""),
      prefs: (companyRow.prefs ?? {}) as Company["prefs"],
      capabilities: capsBy.get(companyId) ?? [],
      references: refsBy.get(companyId) ?? [],
    };

    const partners: Partner[] = companies
      .filter((c) => c.id !== companyId && !c.is_demo)
      .map((c) => ({
        id: String(c.id),
        name: String(c.name ?? ""),
        prefs: {},
        capabilities: capsBy.get(String(c.id)) ?? [],
        references: refsBy.get(String(c.id)) ?? [],
        awards: refsBy.get(String(c.id)) ?? [],
      }));

    const reqsBy = new Map<string, Requirement[]>();
    for (const r of requirements) {
      (reqsBy.get(r.tender_id) ??
        reqsBy.set(r.tender_id, []).get(r.tender_id)!).push(r);
    }

    const resultRows: unknown[] = [];
    const statusRows: unknown[] = [];
    const recRows: unknown[] = [];

    for (const tender of tenders) {
      const out = evaluateTender(
        tender, reqsBy.get(tender.id) ?? [], company, partners, asOf,
      );
      for (const { requirement, result } of out.rows) {
        resultRows.push({
          company_id: companyId,
          requirement_id: requirement.id,
          status: result.status,
          gap_level: result.gap_level ?? null,
          fixable_in_time: result.fixable_in_time ?? null,
          reason: result.reason,
        });
      }
      statusRows.push({
        company_id: companyId,
        tender_id: tender.id,
        label: out.label,
        met: out.met,
        total: out.total,
        gaps: out.gaps,
      });
      for (const r of out.recs) {
        recRows.push({
          company_id: companyId,
          tender_id: tender.id,
          partner_id: r.partner_id,
          rank: r.rank,
          score: r.score,
          gaps_covered: r.gaps_covered,
          joint_met: r.joint_met,
          joint_total: r.joint_total,
          role: r.role,
          evidence: r.evidence,
        });
      }
    }

    // Clear this company's previous verdicts first: a preference change can
    // remove a recommendation, and an upsert alone would leave it behind.
    for (const t of ["results", "tender_status", "partner_recs"]) {
      const { error } = await db.from(t).delete().eq("company_id", companyId);
      if (error) throw new Error(`clearing ${t}: ${error.message}`);
    }
    for (const [table, rows] of [
      ["results", resultRows], ["tender_status", statusRows],
      ["partner_recs", recRows],
    ] as const) {
      for (let i = 0; i < rows.length; i += 500) {
        const { error } = await db.from(table).insert(rows.slice(i, i + 500));
        if (error) throw new Error(`writing ${table}: ${error.message}`);
      }
    }

    const byLabel: Record<string, number> = {};
    for (const s of statusRows as Array<{ label: string }>) {
      byLabel[s.label] = (byLabel[s.label] ?? 0) + 1;
    }

    return json({
      ok: true,
      company: company.name,
      as_of: asOf.toISOString().slice(0, 10),
      tenders: tenders.length,
      results: resultRows.length,
      partner_recs: recRows.length,
      labels: byLabel,
      ms: Date.now() - started,
    });
  } catch (err) {
    return json({ ok: false, error: String((err as Error).message ?? err) }, 500);
  }
});
