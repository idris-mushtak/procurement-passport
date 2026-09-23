"""Shared configuration for the prep pipeline."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
NOTICES = DATA / "notices"
PACKS = DATA / "packs"
CURATED = DATA / "curated"
CACHE = DATA / "cache"
OUT = ROOT / "out"
SQL = ROOT / "sql"

for _d in (NOTICES, PACKS, CURATED, CACHE, OUT):
    _d.mkdir(parents=True, exist_ok=True)

SOURCE_CSV = DATA / "ted_dutch_it_tenders.csv"
SELECTED_JSON = DATA / "selected_tenders.json"

# --- demo parameters -------------------------------------------------------
TODAY = date.fromisoformat(os.getenv("PP_TODAY", date.today().isoformat()))
N_TENDERS = int(os.getenv("PP_N_TENDERS", "12"))
N_PARTNERS = int(os.getenv("PP_N_PARTNERS", "30"))
DEMO_COMPANY_ID = "11111111-1111-1111-1111-111111111111"

# --- external services -----------------------------------------------------
TED_SEARCH = "https://api.ted.europa.eu/v3/notices/search"
TED_NOTICE_XML = "https://ted.europa.eu/en/notice/{}/xml"
TED_NOTICE_HTML = "https://ted.europa.eu/en/notice/-/detail/{}"
TENDERNED_API = "https://www.tenderned.nl/papi/tenderned-rs-tns/v2"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 60

# Nebius Token Factory speaks the OpenAI protocol.
NEBIUS_BASE_URL = os.getenv("NEBIUS_BASE_URL", "https://api.studio.nebius.com/v1")
NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
MODEL_FAST = os.getenv("PP_MODEL_FAST", "deepseek-ai/DeepSeek-V4.1-Flash")
MODEL_STRONG = os.getenv("PP_MODEL_STRONG", "deepseek-ai/DeepSeek-V4-Pro")

# --- extraction ------------------------------------------------------------
# Dutch pages worth sending to the model. Matched case-insensitively.
PAGE_KEYWORDS = [
    "geschiktheidseis", "uitsluitingsgrond", "minimumeis", "referentie",
    "omzet", "certifica", "verzeker", "combinatie", "onderaannem",
    "kerncompetentie", "draagkracht", "beroepsbekwaamheid", "ervaringseis",
    "iso 27001", "iso 9001", "nen 7510",
]
# Documents worth opening at all, by filename. Everything else is contract
# boilerplate, price sheets or annexes that never carry selection criteria.
DOC_NAME_KEYWORDS = [
    "aanbesteding", "leidraad", "selectie", "beschrijvend", "offerteaanvraag",
    "inschrijving", "uitvraag", "gunning", "nota van inlichtingen", "nvi",
]
# The UEA/ESPD is the standard EU self-declaration form. It matches every
# selection-criteria keyword and states no tender-specific threshold, so it
# crowds the real leidraad out of the page budget. Never open it.
DOC_NAME_EXCLUDE = [
    "uea", "espd", "uniform europees", "eigen verklaring",
    "verwerkersovereenkomst", "gibit", "prijzenblad", "sroi", "concept overeenkomst",
    "conceptovereenkomst", "programma van eisen", "pve", "security annex",
    "casus", "scoregrafiek", "areaalgegevens", "verwerkers",
]
MAX_PACK_BYTES = 25 * 1024 * 1024
MAX_PAGES_TO_MODEL = 22
# Per-request ceiling for a model call. Sized so a whole 12-tender extraction
# cannot outlive the build slot it is running in.
LLM_TIMEOUT = float(os.getenv("PP_LLM_TIMEOUT", "150"))

# --- vocabulary shared with the rules engine -------------------------------
REQ_TYPES = (
    "turnover", "certification", "references",
    "insurance", "staff_language", "local_presence", "other",
)
OPERATORS = ("gte", "lte", "eq", "exists", "count_gte")

# capability keys the engine understands
CAP_KEYS = {
    "turnover": ["turnover_avg3y", "turnover_annual"],
    "certification": [
        "iso_27001", "iso_9001", "iso_14001", "iso_22301", "nen_7510",
        "iso_20000", "soc2", "cisco_partner", "microsoft_partner",
    ],
    "insurance": ["liability_insurance_eur", "professional_indemnity_eur"],
    "staff_language": ["lang_nl", "lang_en", "lang_de", "fte", "pm_prince2", "pm_ipma", "scrum_master"],
    "local_presence": ["office_nl", "office_eu"],
    "references": ["reference_count"],
}

TRUST_LEVELS = ("verified", "official", "claim")
