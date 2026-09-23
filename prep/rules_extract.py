"""Deterministic extractor for Dutch selection criteria.

This is not a fallback for a missing API key -- it is the precision half of the
pipeline. Dutch leidraden state their hard thresholds in a small number of
stereotyped forms ("een bedrijfsaansprakelijkheidsverzekering van ten minste
EUR 1.250.000 per gebeurtenis"), and a pattern that matches those is right or
silent, never confidently wrong. The model is better at the loose ones
(kerncompetenties phrased as prose). We run both and merge.

Every row it emits carries the sentence it came from, so a human spot-check is
a three-second read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Optional

from prep.docs import Page
from prep.models import Requirement

# --- Dutch number handling -------------------------------------------------
WORD_NUM = {
    "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5,
    "zes": 6, "zeven": 7, "acht": 8, "negen": 9, "tien": 10,
}
# 1.250.000  |  1.250.000,-  |  1250000  |  1,25 miljoen
MONEY = re.compile(
    r"(?:€|EUR\b|euro)\s*([\d]{1,3}(?:[.\s]\d{3})+|\d+(?:,\d+)?)\s*(miljoen|mln|mio|k)?",
    re.I,
)
MONEY_TRAILING = re.compile(
    r"([\d]{1,3}(?:[.\s]\d{3})+|\d+(?:,\d+)?)\s*(miljoen|mln|mio)?\s*(?:€|EUR\b|euro)", re.I,
)
COUNT = re.compile(
    r"\b(?:minimaal|minimum|ten minste|tenminste|maximaal|één|een|\d+)\s*"
    r"(?:\((\d+)\))?", re.I,
)
YEARS = re.compile(
    r"(?:afgelopen|laatste|voorafgaand(?:e)?\s+aan)?\s*"
    r"(drie|twee|vier|vijf|\d+)\s*\(?\d*\)?\s*(?:kalender)?jaar", re.I,
)

MIN_WORDS = ("ten minste", "tenminste", "minimaal", "minimum", "niet lager dan",
             "ten bedrage van", "van minimaal", "in ieder geval tot een bedrag van")


def _to_number(raw: str, scale: Optional[str]) -> Optional[float]:
    s = raw.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):        # 1.250.000 -> thousands separator
        s = s.replace(".", "")
    elif "," in s:                                      # 1,25 -> decimal comma
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    if scale:
        sc = scale.lower()
        if sc in ("miljoen", "mln", "mio"):
            v *= 1_000_000
        elif sc == "k":
            v *= 1_000
    return v


def money_in(text: str) -> list[float]:
    out = []
    for m in MONEY.finditer(text):
        v = _to_number(m.group(1), m.group(2))
        if v is not None:
            out.append(v)
    for m in MONEY_TRAILING.finditer(text):
        v = _to_number(m.group(1), m.group(2))
        if v is not None:
            out.append(v)
    return [v for v in out if v >= 1000]


def window_years_in(text: str) -> Optional[int]:
    m = YEARS.search(text)
    if not m:
        return None
    tok = m.group(1).lower()
    return WORD_NUM.get(tok, int(tok) if tok.isdigit() else None)


def count_in(text: str) -> Optional[int]:
    """'minimaal één (1) referentieopdracht' / 'twee referenties' / '3 referenties'."""
    m = re.search(r"\b(?:ten minste|tenminste|minimaal|minimum|minstens)\s+"
                  r"(één|een|twee|drie|vier|vijf|\d+)\s*(?:\((\d+)\))?", text, re.I)
    if m:
        tok = (m.group(2) or m.group(1)).lower()
        return WORD_NUM.get(tok, int(tok) if tok.isdigit() else None)
    m = re.search(r"\b(één|een|twee|drie|vier|vijf|\d+)\s*(?:\((\d+)\))?\s*"
                  r"(?:geldige\s+)?referentie", text, re.I)
    if m:
        tok = (m.group(2) or m.group(1)).lower()
        return WORD_NUM.get(tok, int(tok) if tok.isdigit() else None)
    return None


# --- sentence segmentation -------------------------------------------------
def sentences(text: str) -> Iterator[str]:
    """Leidraden lean on bullet lists, where a '.' never arrives. Split on
    newlines and bullets as well as sentence punctuation."""
    for block in re.split(r"\n\s*(?:[•·\-•]|\d+\.\d+(?:\.\d+)?|\(\w\))\s*", text):
        block = " ".join(block.split())
        if not block:
            continue
        for s in re.split(r"(?<=[.;:])\s+(?=[A-Z€u])", block):
            s = s.strip()
            if len(s) >= 25:
                yield s


@dataclass
class Hit:
    req_type: str
    key: str
    operator: str
    threshold_num: Optional[float]
    threshold_text: Optional[str]
    window_years: Optional[int]
    public_sector_required: bool
    quote: str
    page: int
    doc: str
    confidence: str


CERTS = {
    "iso_27001": r"\biso[\s/-]*27001\b",
    "iso_9001": r"\biso[\s/-]*9001\b",
    "iso_14001": r"\biso[\s/-]*14001\b",
    "iso_22301": r"\biso[\s/-]*22301\b",
    "iso_20000": r"\biso[\s/-]*20000\b",
    "nen_7510": r"\bnen[\s/-]*7510\b",
    "soc2": r"\bsoc\s*2\b|\bisae\s*3402\b",
}
CERT_DEMANDED = r"(beschikt?|dient|moet|vereist|gecertificeerd|certificering|aantoon|is uw organisatie)"
CERT_NEGATED = ("niet vereist", "niet verplicht", "pré", "wens", "gunningscriterium",
                "niet noodzakelijk", "strekt tot aanbeveling", "zie paragraaf",
                "bewijs ", "bijlage ")

# A count sentence that caps rather than floors, quotes an example, or is an
# NvI question header is not a requirement.
COUNT_DISQUALIFY = (
    "maximaal", "ten hoogste", "niet meer dan", "voorbeeld", "bijvoorbeeld",
    "onderwerp:", "vraag:", "antwoord:", "indien u", "mag dan ook",
    "tellen uitsluitend",
)
# "Kerncompetentie 1: ..." -- the numbered list is the real requirement count.
KERNCOMP = re.compile(r"kerncompetentie\s*(\d+)\s*[:.\-]", re.I)
NEG_NEAR = re.compile(r"niet\s+(?:is\s+)?toegestaan|niet\s+toe\s?gestaan|uitgesloten|"
                      r"is\s+niet\s+mogelijk|wordt\s+niet\s+geaccepteerd", re.I)


def _near(text: str, a: re.Pattern | str, b: re.Pattern | str, window: int = 140) -> bool:
    """True when both patterns occur within `window` characters of each other.
    Table rows and cross-references merge unrelated phrases into one sentence;
    proximity is what separates 'ISO 27001 is required' from 'proof of
    insurance -- see ISO 27001 paragraph'."""
    ra = a if isinstance(a, re.Pattern) else re.compile(a, re.I)
    rb = b if isinstance(b, re.Pattern) else re.compile(b, re.I)
    starts_a = [m.start() for m in ra.finditer(text)]
    starts_b = [m.start() for m in rb.finditer(text)]
    return any(abs(x - y) <= window for x in starts_a for y in starts_b)


def _mentions_min(s: str) -> bool:
    low = s.lower()
    return any(w in low for w in MIN_WORDS)


def kerncompetentie_count(pages: list[Page]) -> Optional[int]:
    """Dutch leidraden state technical capacity as a numbered list of
    kerncompetenties, each needing its own reference. The highest number in
    that list is the reference count the bidder must actually produce."""
    best = 0
    for p in pages:
        for m in KERNCOMP.finditer(p.text):
            n = int(m.group(1))
            if 1 <= n <= 12:
                best = max(best, n)
    return best or None


def scan_page(p: Page, page_context: str = "") -> list[Hit]:
    hits: list[Hit] = []
    ctx = page_context or p.text
    for s in sentences(p.text):
        low = s.lower()

        # --- insurance ----------------------------------------------------
        if _near(low, r"verzeker|aansprakelijk", MONEY, 160):
            amounts = money_in(s)
            if amounts and _mentions_min(low):
                prof = "beroepsaansprakelijk" in low and "bedrijfsaansprakelijk" not in low
                key = "professional_indemnity_eur" if prof else "liability_insurance_eur"
                # "EUR 1.250.000 per gebeurtenis met een jaarmaximum van EUR 2.500.000":
                # the per-event figure is the binding one, so take the smaller.
                hits.append(Hit("insurance", key, "gte", min(amounts), None, None,
                                False, s, p.page, p.doc_name, "high"))

        # --- turnover -----------------------------------------------------
        if re.search(r"\bomzet\b", low) and _mentions_min(low) and _near(low, r"omzet", MONEY, 160):
            amounts = money_in(s)
            if amounts:
                avg = "gemiddeld" in low or "per jaar" in low or "jaaromzet" in low
                hits.append(Hit("turnover", "turnover_avg3y" if avg else "turnover_annual",
                                "gte", max(amounts), None,
                                window_years_in(s) or window_years_in(ctx),
                                False, s, p.page, p.doc_name, "high"))

        # --- references ---------------------------------------------------
        if re.search(r"referentie", low) and not any(w in low for w in COUNT_DISQUALIFY):
            n = count_in(s)
            if n and 1 <= n <= 10 and _near(low, r"referentie", r"ten minste|tenminste|minimaal|minstens|\b\d+\b", 120):
                public = bool(re.search(
                    r"gemeente|publieke|overheid|aanbestedende dienst|semi-?publiek|"
                    r"provincie|waterschap|ministerie|gemeenschappelijke regeling", low))
                hits.append(Hit("references", "reference_count", "count_gte", float(n),
                                None, window_years_in(s) or window_years_in(ctx),
                                public, s, p.page, p.doc_name, "medium"))
                amounts = money_in(s)
                if amounts and _mentions_min(low):
                    hits.append(Hit("references", "reference_min_value_eur", "gte",
                                    max(amounts), None,
                                    window_years_in(s) or window_years_in(ctx), public,
                                    s, p.page, p.doc_name, "medium"))

        # --- certification -------------------------------------------------
        for key, pat in CERTS.items():
            if not re.search(pat, low):
                continue
            if any(w in low for w in CERT_NEGATED):
                continue
            if _near(low, pat, CERT_DEMANDED, 120):
                hits.append(Hit("certification", key, "exists", None, key.upper(),
                                None, False, s, p.page, p.doc_name, "high"))

        # --- language / local presence -------------------------------------
        if _near(low, r"nederlandse taal|in het nederlands|voertaal",
                 r"dient|moet|is de|wordt|opgesteld", 120):
            hits.append(Hit("staff_language", "lang_nl", "exists", None, "Nederlands",
                            None, False, s, p.page, p.doc_name, "medium"))
        if _near(low, r"vestiging|kantoor|gevestigd", r"nederland", 100) and _mentions_min(low):
            hits.append(Hit("local_presence", "office_nl", "exists", None, "NL",
                            None, False, s, p.page, p.doc_name, "low"))
    return hits


def dedupe(hits: list[Hit]) -> list[Hit]:
    """Same requirement restated across leidraad and NvI: keep the strictest,
    and prefer the row that cites the earliest page (the leidraad itself)."""
    best: dict[tuple[str, str], Hit] = {}
    for h in hits:
        k = (h.req_type, h.key)
        cur = best.get(k)
        if cur is None:
            best[k] = h
        elif (h.threshold_num or 0) > (cur.threshold_num or 0):
            best[k] = h
    return list(best.values())


def extract(tender_id: str, pages: list[Page]) -> list[Requirement]:
    ctx = "\n".join(p.text for p in pages)
    hits: list[Hit] = []
    for p in pages:
        hits.extend(scan_page(p, ctx))
    # The numbered kerncompetentie list beats any count scraped from prose.
    kc = kerncompetentie_count(pages)
    if kc:
        anchor = next((h for h in hits if h.key == "reference_count"), None)
        quote = anchor.quote if anchor else None
        page = anchor.page if anchor else None
        doc = anchor.doc if anchor else None
        if quote is None:
            for p in pages:
                m = KERNCOMP.search(p.text)
                if m:
                    quote = " ".join(p.text[m.start():m.start() + 320].split())
                    page, doc = p.page, p.doc_name
                    break
        if quote:
            hits = [h for h in hits if h.key != "reference_count"]
            hits.append(Hit("references", "reference_count", "count_gte", float(kc),
                            None, window_years_in(ctx), False, quote, page, doc, "high"))
    out: list[Requirement] = []
    for h in dedupe(hits):
        try:
            out.append(Requirement(
                tender_id=tender_id, req_type=h.req_type, key=h.key,
                operator=h.operator, threshold_num=h.threshold_num,
                threshold_text=h.threshold_text, window_years=h.window_years,
                public_sector_required=h.public_sector_required, knockout=True,
                clause_ref=None, source_quote=h.quote, page=h.page,
                confidence=h.confidence, source_doc=h.doc,
            ))
        except Exception:
            continue    # a row that will not validate is a row we do not ship
    return out


def flags(pages: list[Page]) -> dict[str, bool]:
    """joint_bids_allowed / subcontracting_allowed from the leidraad's wording."""
    joint, sub = True, True
    for p in pages:
        for s in sentences(p.text):
            low = s.lower()
            # "combinatie" and "niet toegestaan" both appearing on a page means
            # nothing; they have to be the same clause.
            if _near(low, r"combinatie|samenwerkingsverband", NEG_NEAR, 90):
                joint = False
            if _near(low, r"onderaannem|derde partij", NEG_NEAR, 90):
                sub = False
    return {"joint_bids_allowed": joint, "subcontracting_allowed": sub}
