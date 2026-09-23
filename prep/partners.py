"""Build the partner candidate pool from TED award notices.

Award notices are the strongest evidence available for free: they say, in a
published EU record, that this company won this contract for this buyer at this
value. That is why partner references land with `trust = official` and the UI
can show a source URL next to every claim.

What award data cannot tell us is whether a partner holds ISO 27001 or has a
Dutch-speaking office. Those are hand-entered from company websites into
`data/curated/partner_facts.csv` with `trust = claim`, and the UI labels them
differently. We never let a claim masquerade as a verified fact.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator, Optional

import requests

from prep import config as C

CPV_QUERY = "classification-cpv=72* OR classification-cpv=48*"
FIELDS = [
    "publication-number", "winner-name", "buyer-name", "classification-cpv",
    "publication-date", "total-value", "notice-title", "winner-decision-date",
]
LEGAL_SUFFIX = re.compile(
    r"\b(b\.?v\.?|n\.?v\.?|v\.?o\.?f\.?|c\.?v\.?|holding|group|groep|nederland|"
    r"netherlands|international|s\.?a\.?|gmbh|ltd|inc|sarl|coöperatie|cooperatie|"
    r"u\.?a\.?)\b\.?", re.I)
PUBLIC_HINTS = (
    "gemeente", "provincie", "ministerie", "waterschap", "rijkswaterstaat",
    "politie", "belastingdienst", "universiteit", "hogeschool", "stichting",
    "veiligheidsregio", "omgevingsdienst", "ggd", "ziekenhuis", "umc",
    "rijk", "dienst", "kadaster", "duo", "cbr", "rdw", "surf", "kvk",
    "gemeenschappelijke regeling", "samenwerking", "raad", "kamer", "agentschap",
    "autoriteit", "inspectie", "bureau", "college", "regio", "vervoerregio",
)
# Firms that are never realistic hackathon-demo partners for an SME.
TOO_BIG = (
    "capgemini", "accenture", "atos", "ibm", "microsoft", "kpmg", "deloitte",
    "ernst & young", "pwc", "sopra", "tata consultancy", "infosys", "cognizant",
    "t-systems", "fujitsu", "dxc", "ordina", "conclusion",
)


def norm_name(raw: str) -> str:
    s = raw.lower().replace("&", "en")
    s = LEGAL_SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def is_public(buyer: str) -> bool:
    low = (buyer or "").lower()
    return any(h in low for h in PUBLIC_HINTS)


@dataclass
class Award:
    publication: str
    winner: str
    buyer: str
    value_eur: Optional[float]
    cpv: list[str]
    published: Optional[date]
    title: Optional[str]

    @property
    def url(self) -> str:
        return C.TED_NOTICE_HTML.format(self.publication)


@dataclass
class PartnerCandidate:
    slug: str
    name: str
    awards: list[Award] = field(default_factory=list)

    @property
    def public_awards(self) -> list[Award]:
        return [a for a in self.awards if is_public(a.buyer)]

    @property
    def largest(self) -> Optional[float]:
        vals = [a.value_eur for a in self.awards if a.value_eur]
        return max(vals) if vals else None

    @property
    def cpv_divisions(self) -> set[str]:
        return {c[:2] for a in self.awards for c in a.cpv if c}


# TED award values are self-reported and occasionally nonsense: a EUR 19.6bn
# software contract, or the literal keyboard-walk 123456789. A bad value would
# silently wreck the size_fit term in the partner score, so we drop rather than
# keep anything outside a plausible band.
VALUE_CEILING = 500_000_000
PLACEHOLDER_VALUES = {123456789, 1234567, 12345678, 111111111, 999999999,
                      1000000000, 100000000000}


def plausible_value(v: Optional[float]) -> Optional[float]:
    if v is None or v <= 1000 or v > VALUE_CEILING:
        return None
    if int(v) in PLACEHOLDER_VALUES:
        return None
    return v


def _first(v):
    if isinstance(v, dict):
        v = next(iter(v.values()), None)
    if isinstance(v, list):
        v = v[0] if v else None
    return v


def search_awards(since: str = "20240101", pages: int = 14,
                  per_page: int = 100) -> Iterator[Award]:
    """TED expert search, paged. Only eForms-era notices carry `winner-name`,
    which is why we start at 2024 rather than reaching further back for volume."""
    query = (f"({CPV_QUERY}) AND buyer-country=NLD AND notice-type=can-standard "
             f"AND publication-date>={since}")
    for page in range(1, pages + 1):
        body = {"query": query, "fields": FIELDS, "limit": per_page,
                "page": page, "scope": "ALL"}
        for attempt in range(3):
            try:
                r = requests.post(C.TED_SEARCH, json=body, timeout=90,
                                  headers={"User-Agent": C.USER_AGENT})
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        else:
            return
        notices = r.json().get("notices") or []
        if not notices:
            return
        for n in notices:
            winner = _first(n.get("winner-name"))
            if not winner:
                continue
            pub = _first(n.get("publication-date")) or ""
            try:
                value = float(n.get("total-value"))
            except (TypeError, ValueError):
                value = None
            yield Award(
                publication=n.get("publication-number", ""),
                winner=str(winner).strip(),
                buyer=str(_first(n.get("buyer-name")) or "").strip(),
                value_eur=plausible_value(value),
                cpv=[c for c in (n.get("classification-cpv") or []) if c],
                published=date.fromisoformat(pub[:10]) if pub[:4].isdigit() else None,
                title=str(_first(n.get("notice-title")) or "")[:200] or None,
            )
        time.sleep(0.4)


def build_pool(awards: Iterator[Award]) -> list[PartnerCandidate]:
    pool: dict[str, PartnerCandidate] = {}
    for a in awards:
        slug = norm_name(a.winner)
        if not slug or len(slug) < 3:
            continue
        if any(b in slug for b in TOO_BIG):
            continue
        cand = pool.get(slug)
        if cand is None:
            cand = pool[slug] = PartnerCandidate(slug=slug, name=a.winner.strip())
        cand.awards.append(a)
    return list(pool.values())


def rank(pool: list[PartnerCandidate], n: int = C.N_PARTNERS) -> list[PartnerCandidate]:
    """2+ public-sector awards is the design doc's bar. Within that, prefer
    partners with breadth (more buyers) over one big repeat customer."""
    eligible = [c for c in pool if len(c.public_awards) >= 2]
    eligible.sort(key=lambda c: (
        -len({a.buyer for a in c.public_awards}),
        -len(c.public_awards),
        -(c.largest or 0),
    ))
    return eligible[:n]


def partner_id(slug: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"procurement-passport:partner:{slug}"))


def main() -> int:
    cache = C.CACHE / "awards.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
        awards = [Award(**{**a, "published": date.fromisoformat(a["published"])
                           if a["published"] else None}) for a in raw]
        print(f"loaded {len(awards)} cached awards")
    else:
        awards = list(search_awards())
        cache.write_text(json.dumps(
            [{**a.__dict__, "published": a.published.isoformat() if a.published else None}
             for a in awards], indent=2))
        print(f"fetched {len(awards)} award notices from TED")

    pool = build_pool(iter(awards))
    picked = rank(pool)
    out = C.DATA / "partners.json"
    out.write_text(json.dumps([{
        "id": partner_id(c.slug), "slug": c.slug, "name": c.name,
        "public_awards": len(c.public_awards),
        "buyers": sorted({a.buyer for a in c.public_awards}),
        "largest_award_eur": c.largest,
        "cpv_divisions": sorted(c.cpv_divisions),
        "references": [{
            "buyer": a.buyer, "value_eur": a.value_eur,
            "end_date": a.published.isoformat() if a.published else None,
            "cpv": a.cpv[0] if a.cpv else None, "title": a.title,
            "source_url": a.url, "public_sector": is_public(a.buyer),
        } for a in c.public_awards],
    } for c in picked], indent=2))
    print(f"{len(pool)} distinct winners -> {len(picked)} partner candidates -> {out}")
    for c in picked[:15]:
        big = f"{c.largest:,.0f}" if c.largest else "-"
        print(f"  {len(c.public_awards):2d} awards  EUR {big:>13s}  "
              f"cpv{sorted(c.cpv_divisions)}  {c.name[:44]}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
