-- The demo company.
--
-- Fictional, but shaped like a real Dutch IT SME: profitable, competent,
-- certified for quality but not for security, with a short public track
-- record. That shape is the whole point -- it is the profile that loses
-- tenders for reasons nobody told it about.
--
-- Generated from data/curated/demo_company.json. Edit that file, not this one,
-- then re-run `python -m prep.run emit`. Tuning the demo profile so the gaps
-- spread across the ladder is fair game; tuning extracted requirements is not.


begin;

insert into companies (id, name, is_demo, country, slug, prefs)
values ('11111111-1111-1111-1111-111111111111', 'Meridiaan Digitaal B.V.', true, 'NL',
        'demo-meridiaan', '{"allow_certification": true, "allow_partners": true, "partner_min_contract_value_eur": 0, "excluded_partners": []}'::jsonb)
on conflict (id) do update set name = excluded.name, prefs = excluded.prefs;

delete from capabilities where company_id = '11111111-1111-1111-1111-111111111111';

insert into capabilities (company_id, req_type, key, value_num, value_text,
                          valid_until, trust, source_quote)
values ('11111111-1111-1111-1111-111111111111', 'turnover', 'turnover_avg3y', 1400000,
        null, null,
        'verified', 'Jaarrekening 2023-2025, gemiddelde omzet EUR 1.4M');
insert into capabilities (company_id, req_type, key, value_num, value_text,
                          valid_until, trust, source_quote)
values ('11111111-1111-1111-1111-111111111111', 'certification', 'iso_9001', null,
        'ISO 9001:2015', '2027-06-30',
        'verified', 'Certificaat ISO 9001:2015, geldig tot 30-06-2027');
insert into capabilities (company_id, req_type, key, value_num, value_text,
                          valid_until, trust, source_quote)
values ('11111111-1111-1111-1111-111111111111', 'insurance', 'liability_insurance_eur', 1000000,
        null, '2027-01-01',
        'verified', 'Polis bedrijfsaansprakelijkheid EUR 1.000.000 per gebeurtenis');
insert into capabilities (company_id, req_type, key, value_num, value_text,
                          valid_until, trust, source_quote)
values ('11111111-1111-1111-1111-111111111111', 'staff_language', 'lang_nl', null,
        'Nederlands', null,
        'verified', 'Voertaal van de organisatie is Nederlands');
insert into capabilities (company_id, req_type, key, value_num, value_text,
                          valid_until, trust, source_quote)
values ('11111111-1111-1111-1111-111111111111', 'local_presence', 'office_nl', null,
        'Amsterdam, NL', null,
        'verified', 'Vestiging Amsterdam, KvK-inschrijving Nederland');
insert into capabilities (company_id, req_type, key, value_num, value_text,
                          valid_until, trust, source_quote)
values ('11111111-1111-1111-1111-111111111111', 'staff_language', 'fte', 18,
        null, null,
        'verified', '18 FTE per 1 januari 2026');

delete from company_references where company_id = '11111111-1111-1111-1111-111111111111';

insert into company_references (company_id, buyer, title, public_sector, cpv,
                  value_eur, end_date, trust)
values ('11111111-1111-1111-1111-111111111111', 'Gemeente Haarlemmermeer', 'Implementatie zaakgericht werken en koppelingen',
        true, '72212000',
        240000, '2025-04-30',
        'verified');
insert into company_references (company_id, buyer, title, public_sector, cpv,
                  value_eur, end_date, trust)
values ('11111111-1111-1111-1111-111111111111', 'Omgevingsdienst Noordzeekanaalgebied', 'Beheer en doorontwikkeling inspectieapplicatie',
        true, '72267000',
        310000, '2026-02-28',
        'verified');
insert into company_references (company_id, buyer, title, public_sector, cpv,
                  value_eur, end_date, trust)
values ('11111111-1111-1111-1111-111111111111', 'Koninklijke Mosa B.V.', 'Integratieplatform productieregistratie',
        false, '72212000',
        180000, '2024-11-30',
        'verified');

commit;
