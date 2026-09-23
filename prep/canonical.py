"""Collapse extracted requirement keys onto one shared vocabulary.

The model names the same concept differently in every tender -- a company's
financial soundness arrives as `financial_continuity`, `financial_draagkracht`,
`financial_health`, `stabiliteit_continuiteit` and
`accountantsverklaring_geen_continuiteitsparagraaf`. Each is a fair reading of
its own clause, but `gap_summary` groups by `key`, so five spellings become
five rows each blocking one tender instead of one row blocking five. The
headline "this gap costs you EUR X" is only true if the keys line up.

This also fixes classification drift: the Gedragsverklaring Aanbesteden came
back as `certification` on one tender and `other` on another, which put the
same requirement on two different ladder rungs.
"""
from __future__ import annotations

import re

# canonical key -> patterns that should collapse onto it
SYNONYMS: dict[str, list[str]] = {
    # --- administrative attestations (ladder 1: days of paperwork) ---------
    "gva": [r"^gva$", r"gedragsverklaring"],
    "handelsregister_uittreksel": [
        r"handelsregister", r"^trade_register", r"kvk_uittreksel", r"^kvk$"],
    "belastingdienst_verklaring": [
        r"belastingdienst", r"verklaring_betalingsgedrag", r"betaling.*belasting",
        r"tax_payment"],
    "russia_sanctions_declaration": [
        r"russi", r"rusland", r"sanctie"],
    "financial_soundness": [
        r"financial_(continuity|health|draagkracht|soundness)",
        r"stabiliteit", r"continuiteitsparagraaf", r"no_negative_continuity",
        r"accountantsverklaring"],
    "power_of_attorney": [r"volmacht", r"power_of_attorney"],

    # --- insurance --------------------------------------------------------
    "liability_insurance_eur": [
        r"^liability_insurance$", r"bedrijfsaansprakelijk", r"^liability_insurance_eur$"],
    "professional_indemnity_eur": [r"beroepsaansprakelijk", r"^professional_indemnity_eur$"],

    # --- certification ----------------------------------------------------
    "iso_27001": [r"iso.?27001", r"iso_iec_27001"],
    "iso_9001": [r"iso.?9001"],
    "iso_14001": [r"iso.?14001"],
    "iso_20000_1": [r"iso.?20000"],
    "nen_7510": [r"nen.?7510"],
    "quality_management_system": [r"quality_management", r"kwaliteitszorg", r"kwaliteitsmanagement"],
}

# Requirement types that a key forces, regardless of what the model said.
FORCED_TYPE: dict[str, str] = {
    "gva": "certification",
    "handelsregister_uittreksel": "certification",
    "belastingdienst_verklaring": "certification",
    "russia_sanctions_declaration": "other",
    "financial_soundness": "other",
    "power_of_attorney": "certification",
    "liability_insurance_eur": "insurance",
    "professional_indemnity_eur": "insurance",
    "quality_management_system": "certification",
}

# Keys whose "threshold" is a count of something other than money or
# references -- a policy's minimum number of payouts per year, say. Comparing
# those against a capability would be nonsense, so they are judgement items.
FORCE_REVIEW = [
    r"occurrences?$", r"_per_jaar$", r"uitkeringen",
    r"^reference_(window|max_age)", r"_akkoord_", r"^ipi_", r"^wto_",
]

# Kerncompetentie N: real requirements, but each names tender-specific work and
# will never match a capability row. They belong on the review list, where a
# human reads the clause, rather than counted as an automatic gap.
KERNCOMPETENTIE = re.compile(r"kerncompetentie|core_competence|reference_(s\d|citrix|social|org_size)")


def canonical_key(key: str) -> str:
    k = key.strip().lower()
    for canon, pats in SYNONYMS.items():
        for p in pats:
            if re.search(p, k):
                return canon
    return k


def canonicalise(row: dict) -> dict:
    """Apply to one requirement dict (post-validation, pre-emit)."""
    row = dict(row)
    original = row["key"]
    key = canonical_key(original)
    row["key"] = key

    forced = FORCED_TYPE.get(key)
    if forced:
        row["req_type"] = forced

    if any(re.search(p, original.lower()) for p in FORCE_REVIEW) or \
            KERNCOMPETENTIE.search(original.lower()):
        row["req_type"] = "other"
        row["knockout"] = False

    if key != original:
        row["original_key"] = original
    return row


def apply(rows: list[dict]) -> tuple[list[dict], dict]:
    """Canonicalise and drop the duplicates that collapsing creates.

    Two clauses on the same tender that both mean "be financially sound" become
    one requirement. Keep the stricter threshold and the earlier page, so the
    citation still points at the clause a reader would find first.
    """
    out: dict[tuple[str, str, str], dict] = {}
    stats = {"renamed": 0, "merged": 0, "forced_review": 0}
    for raw in rows:
        row = canonicalise(raw)
        if "original_key" in row:
            stats["renamed"] += 1
        if row["req_type"] == "other" and raw.get("req_type") != "other":
            stats["forced_review"] += 1
        k = (row["ted_id"], row["req_type"], row["key"])
        cur = out.get(k)
        if cur is None:
            out[k] = row
            continue
        stats["merged"] += 1
        if (row.get("threshold_num") or 0) > (cur.get("threshold_num") or 0):
            out[k] = {**row, "page": min(
                p for p in (row.get("page"), cur.get("page")) if p is not None)}
        elif cur.get("page") and row.get("page") and row["page"] < cur["page"]:
            cur["page"] = row["page"]
    return list(out.values()), stats
