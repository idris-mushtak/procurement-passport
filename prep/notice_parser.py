"""Parse a TED eForms UBL notice into the fields the `tenders` table needs.

Everything here is deterministic -- no model involved. The notice reliably
carries title, buyer, CPV, value, deadline, exclusion grounds and the link to
the procurement document pack. It does *not* carry selection criteria for
93 of our 95 notices (they point at the tender documents or the ESPD instead),
which is exactly why the LLM step reads the pack.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "efac": "http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1",
    "efbc": "http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}
DUTCH = ("NLD", "nld", "nl")


def _date(raw: Optional[str]) -> Optional[date]:
    """TED stamps a UTC offset onto dates: 2026-11-19+01:00."""
    if not raw:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw.strip())
    return date.fromisoformat(m.group(1)) if m else None


def _pref_lang(elements: list[ET.Element]) -> Optional[str]:
    """Prefer the Dutch rendering of a multilingual field, else the first."""
    if not elements:
        return None
    for e in elements:
        if (e.get("languageID") or "") in DUTCH and (e.text or "").strip():
            return e.text.strip()
    for e in elements:
        if (e.text or "").strip():
            return e.text.strip()
    return None


class Notice:
    def __init__(self, ted_id: str, root: ET.Element):
        self.ted_id = ted_id
        self.root = root

    # -- helpers ----------------------------------------------------------
    def _find(self, path: str) -> Optional[ET.Element]:
        return self.root.find(path, NS)

    def _all(self, path: str) -> list[ET.Element]:
        return self.root.findall(path, NS)

    # -- fields -----------------------------------------------------------
    @property
    def title(self) -> Optional[str]:
        return _pref_lang(self._all(".//cac:ProcurementProject/cbc:Name"))

    @property
    def buyer(self) -> Optional[str]:
        for org in self._all(".//cac:PartyName/cbc:Name"):
            if (org.text or "").strip():
                return org.text.strip()
        return None

    @property
    def description(self) -> Optional[str]:
        return _pref_lang(self._all(".//cac:ProcurementProject/cbc:Description"))

    @property
    def cpv(self) -> list[str]:
        seen, out = set(), []
        for e in self._all(".//cbc:ItemClassificationCode"):
            c = (e.text or "").strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    @property
    def value_eur(self) -> Optional[float]:
        for path in (
            ".//cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount",
            ".//efbc:FrameworkMaximumAmount",
        ):
            for e in self._all(path):
                try:
                    v = float(e.text)
                except (TypeError, ValueError):
                    continue
                if v > 1:  # TED placeholders of 0 or 1 are not values
                    return v
        return None

    @property
    def deadline(self) -> Optional[date]:
        for path in (
            ".//cac:TenderSubmissionDeadlinePeriod/cbc:EndDate",
            ".//cac:ParticipationRequestReceptionPeriod/cbc:EndDate",
        ):
            e = self._find(path)
            if e is not None:
                return _date(e.text)
        return None

    @property
    def documents_url(self) -> Optional[str]:
        best = None
        for ref in self._all(".//cac:CallForTendersDocumentReference"):
            uri = ref.find(".//cbc:URI", NS)
            if uri is None or not (uri.text or "").strip():
                continue
            dt = ref.find("cbc:DocumentType", NS)
            kind = (dt.text or "") if dt is not None else ""
            if kind == "non-restricted-document":
                return uri.text.strip()
            best = best or uri.text.strip()
        return best

    @property
    def exclusion_grounds(self) -> list[tuple[str, str]]:
        """(code, Dutch description) for every exclusion ground stated in the notice."""
        out = []
        for req in self._all(".//cac:SpecificTendererRequirement"):
            code = req.find("cbc:TendererRequirementTypeCode", NS)
            if code is None or code.get("listName") != "exclusion-ground":
                continue
            desc = _pref_lang(req.findall("cbc:Description", NS)) or ""
            out.append(((code.text or "").strip(), " ".join(desc.split())))
        return out

    @property
    def selection_criteria(self) -> list[tuple[str, str]]:
        """Only 2 of 95 notices populate this; the rest defer to the pack."""
        out = []
        for sc in self._all(".//efac:SelectionCriteria"):
            code = sc.find("cbc:TendererRequirementTypeCode", NS)
            desc = _pref_lang(sc.findall("cbc:Description", NS)) or ""
            out.append(((code.text or "").strip() if code is not None else "",
                        " ".join(desc.split())))
        return out

    @property
    def criteria_source(self) -> str:
        """epo-notice | epo-procurement-document | epo-sub-espd"""
        for req in self._all(".//cac:SpecificTendererRequirement"):
            code = req.find("cbc:TendererRequirementTypeCode", NS)
            if code is not None and code.get("listName") == "selection-criteria-source":
                return (code.text or "").strip()
        return "unknown"

    @property
    def winners(self) -> list[str]:
        """Award notices only: the organisations that won."""
        out = []
        for e in self._all(".//efac:TenderingParty/efac:Tenderer"):
            oid = e.find("cbc:ID", NS)
            if oid is not None and oid.text:
                out.append(oid.text.strip())
        return out


def load(path: Path) -> Notice:
    ted_id = path.stem
    return Notice(ted_id, ET.parse(path).getroot())
