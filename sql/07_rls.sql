-- Row Level Security for Supabase.
--
-- Stock Postgres does not need this, so tools/load_test.py never surfaced it.
-- Supabase does: with RLS disabled, every table here is readable AND writable
-- by anyone holding the anon key, and the project's Security Advisor will flag
-- each one. The Lovable app only ever reads, and prep.py writes over a direct
-- connection or the service role, so public-read / no-public-write is exactly
-- the policy set we want.
--
-- Safe to re-run.

alter table companies     enable row level security;
alter table judgments     enable row level security;
alter table llm_calls     enable row level security;
alter table capabilities  enable row level security;
alter table company_references          enable row level security;
alter table tenders       enable row level security;
alter table requirements  enable row level security;
alter table ladder        enable row level security;
alter table results       enable row level security;
alter table tender_status enable row level security;
alter table partner_recs  enable row level security;

-- Read-only for the frontend. The service role bypasses RLS entirely, so
-- prep.py and the /evaluate Edge Function are unaffected by these policies.
do $$
declare t text;
begin
  foreach t in array array[
    'companies','capabilities','company_references','tenders','requirements',
    'ladder','results','tender_status','partner_recs','judgments','llm_calls'
  ] loop
    execute format('drop policy if exists %I on %I', 'public_read_' || t, t);
    execute format(
      'create policy %I on %I for select to anon, authenticated using (true)',
      'public_read_' || t, t);
  end loop;
end $$;

-- Deliberately no insert/update/delete policy: writes go through the service
-- role. The one thing the browser must change is a company's preferences, and
-- that goes through a function rather than a table policy -- a policy wide
-- enough to allow the prefs update would also allow rewriting `name`,
-- `is_demo` and `excluded_partners`.
create or replace function update_prefs(p_company_id uuid, p_prefs jsonb)
returns companies
language plpgsql
security definer
set search_path = public
as $fn$
declare
  updated companies;
begin
  if p_prefs is null or jsonb_typeof(p_prefs) <> 'object' then
    raise exception 'prefs must be a JSON object';
  end if;

  -- Whitelist the keys. Anything else the caller sends is ignored rather than
  -- stored, so this function can never become a general-purpose jsonb writer.
  update companies
     set prefs = jsonb_strip_nulls(
           jsonb_build_object(
             'allow_certification',
               coalesce(p_prefs -> 'allow_certification', prefs -> 'allow_certification', 'true'::jsonb),
             'allow_partners',
               coalesce(p_prefs -> 'allow_partners', prefs -> 'allow_partners', 'true'::jsonb),
             'partner_min_value',
               coalesce(p_prefs -> 'partner_min_value', prefs -> 'partner_min_value', '0'::jsonb),
             'partner_min_contract_value_eur',
               coalesce(p_prefs -> 'partner_min_contract_value_eur',
                        prefs -> 'partner_min_contract_value_eur', '0'::jsonb),
             'excluded_partners',
               coalesce(p_prefs -> 'excluded_partners', prefs -> 'excluded_partners', '[]'::jsonb)
           ))
   where id = p_company_id
  returning * into updated;

  if not found then
    raise exception 'company % not found', p_company_id;
  end if;
  return updated;
end;
$fn$;

revoke all on function update_prefs(uuid, jsonb) from public;
grant execute on function update_prefs(uuid, jsonb) to anon, authenticated;
