-- Paste into the Supabase SQL editor AFTER loading the bundle.
-- Every row should read OK. Anything reading FAIL tells you exactly what
-- is missing, rather than leaving you to guess from a silent editor.

with expected(label, got, want) as (
  select 'tenders',                (select count(*) from tenders),            12
  union all select 'requirements', (select count(*) from requirements),      110
  union all select 'requirements with a clause cite',
                                   (select count(*) from requirements
                                    where source_quote <> '' and page is not null), 110
  union all select 'companies (30 partners + 1 demo)',
                                   (select count(*) from companies),          31
  union all select 'partner references',
                                   (select count(*) from company_references),              295
  union all select 'demo capabilities',
                                   (select count(*) from capabilities
                                    where company_id = '11111111-1111-1111-1111-111111111111'), 6
  union all select 'partner cert claims',
                                   (select count(*) from capabilities where trust = 'claim'), 18
  union all select 'ladder levels', (select count(*) from ladder),             6
  union all select 'tables with RLS on',
                                   (select count(*) from pg_tables
                                    where schemaname = 'public' and rowsecurity), 9
  union all select 'public read policies',
                                   (select count(*) from pg_policies
                                    where schemaname = 'public'),              9
  union all select 'orphan requirements (want 0)',
                                   (select count(*) from requirements r
                                    left join tenders t on t.id = r.tender_id
                                    where t.id is null),                       0
  union all select 'duplicate ted_ids (want 0)',
                                   (select count(*) from (
                                      select ted_id from tenders
                                      group by ted_id having count(*) > 1) x),  0
)
select case when got = want then 'OK  ' else 'FAIL' end as status,
       label, got, want
from expected
order by (got = want), label;
