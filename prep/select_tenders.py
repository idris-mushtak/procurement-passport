"""Pick the demo tender set.

The demo needs tenders that (a) are really open, (b) have a downloadable
document pack so requirements can be extracted rather than invented, (c) carry
a contract value so the headline "N tenders worth EUR X" is honest, and
(d) spread across deadline runway so the ladder's fixable-in-time check has
something to say -- a set where every deadline is 60 days out proves nothing.
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from prep import config as C
from prep.notice_parser import load as load_notice

IT_PREFIXES = ("72", "48", "302", "3232", "5031", "5032", "6421", "7231")


@dataclass
class Candidate:
    ted_id: str
    title: str
    buyer: str
    value_eur: float | None
    deadline: str | None
    days_until: int | None
    cpv: list[str]
    portal: str
    documents_url: str | None
    criteria_source: str
    pack_host: str | None
    score: float
    reasons: list[str]


def _pack_host(url: str | None) -> str | None:
    if not url:
        return None
    if "tenderned.nl" in url:
        return "tenderned"
    if "mercell.com" in url:
        return "mercell"
    if "ec.europa.eu" in url:
        return "eu-portal"
    return "other"


def _is_it(cpv: list[str], csv_flag: str) -> bool:
    if any(c.startswith(p) for c in cpv for p in IT_PREFIXES):
        return True
    return csv_flag == "yes"


def build_candidates() -> list[Candidate]:
    rows = {r["ted_id"]: r for r in csv.DictReader(C.SOURCE_CSV.open(encoding="utf-8"))}
    out: list[Candidate] = []
    for ted_id, row in rows.items():
        path = C.NOTICES / f"{ted_id}.xml"
        if not path.exists():
            continue
        n = load_notice(path)
        deadline = n.deadline
        days = (deadline - C.TODAY).days if deadline else None
        host = _pack_host(n.documents_url)

        reasons, score = [], 0.0
        if not _is_it(n.cpv, row.get("is_it", "")):
            continue
        if days is None:
            continue
        if days < 7:
            continue  # too close to bid for; nothing is fixable
        # Deadline runway is the point of the ladder, so reward a real spread.
        score += 2.0 if days >= 45 else 1.2 if days >= 25 else 0.6
        reasons.append(f"{days}d runway")

        if host == "tenderned":
            score += 3.0                      # open API, pack is downloadable
            reasons.append("tenderned pack")
        elif host == "eu-portal":
            score += 0.5
            reasons.append("eu portal pack")
        elif host == "mercell":
            score += 0.3
            reasons.append("mercell (login)")

        if n.criteria_source == "epo-notice":
            score += 2.0
            reasons.append("criteria in notice")

        v = n.value_eur
        if v:
            score += 1.5
            reasons.append(f"value EUR {v:,.0f}")
            if 250_000 <= v <= 25_000_000:
                score += 1.0   # SME-plausible; a 700M EU frame is not a demo bid
                reasons.append("SME-plausible size")

        out.append(Candidate(
            ted_id=ted_id, title=n.title or row["title"], buyer=n.buyer or row["buyer"],
            value_eur=v, deadline=deadline.isoformat() if deadline else None,
            days_until=days, cpv=n.cpv, portal=row.get("portal", ""),
            documents_url=n.documents_url, criteria_source=n.criteria_source,
            pack_host=host, score=round(score, 2), reasons=reasons,
        ))
    return sorted(out, key=lambda c: -c.score)


def select(n: int = C.N_TENDERS) -> list[Candidate]:
    """Top-scoring set, capped at 2 tenders per buyer so the demo list does not
    turn into one municipality's procurement calendar."""
    picked: list[Candidate] = []
    per_buyer: dict[str, int] = {}
    for c in build_candidates():
        if per_buyer.get(c.buyer, 0) >= 2:
            continue
        picked.append(c)
        per_buyer[c.buyer] = per_buyer.get(c.buyer, 0) + 1
        if len(picked) == n:
            break
    return picked


def main() -> int:
    picked = select()
    C.SELECTED_JSON.write_text(json.dumps([asdict(c) for c in picked], indent=2))
    total = sum(c.value_eur or 0 for c in picked)
    print(f"selected {len(picked)} tenders, EUR {total:,.0f} total -> {C.SELECTED_JSON}")
    for c in picked:
        v = f"{c.value_eur:,.0f}" if c.value_eur else "-"
        print(f"  {c.ted_id}  {c.score:5.2f}  EUR {v:>12s}  {c.days_until:>3d}d  "
              f"{(c.pack_host or '-'):10s} {c.title[:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
