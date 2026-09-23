"""Find evidence for partner capabilities that award data cannot prove.

TED tells us who won what. It says nothing about ISO 27001, NEN 7510 or where
a company keeps an office -- and those are exactly the gaps a partner is meant
to close. The design doc's answer is to hand-enter those facts from company
websites at `trust = claim` with a source URL.

This script does the legwork for that hand-entry: it fetches the partner's site
and records where a certification is actually stated, with the URL and the
sentence. It never asserts a certification it did not read. Anything it cannot
find is left blank for a human, not guessed -- a fabricated ISO claim about a
named real company is the one output this project must never produce.

Output: data/curated/partner_facts.csv  (review by hand, then use in prep)
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests

from prep import config as C

CERT_PATTERNS = {
    "iso_27001": r"iso[\s/‑-]*27001",
    "iso_9001": r"iso[\s/‑-]*9001",
    "iso_14001": r"iso[\s/‑-]*14001",
    "iso_22301": r"iso[\s/‑-]*22301",
    "nen_7510": r"nen[\s/‑-]*7510",
    "iso_20000": r"iso[\s/‑-]*20000",
    "soc2": r"\bsoc\s*2\b|isae\s*3402",
}
# Pages that state certifications, in the order worth trying.
CANDIDATE_PATHS = [
    "", "/certificeringen", "/certificering", "/over-ons", "/about",
    "/kwaliteit", "/informatiebeveiliging", "/security", "/compliance",
    "/organisatie", "/over-ons/certificeringen",
]
NEGATIVE = ("niet gecertificeerd", "not certified", "working towards",
            "in aanvraag", "streven naar")
# A page that merely names ISO 27001 -- a services page, a blog, a compliance
# explainer -- is not evidence that this company holds it. Require language of
# possession near the match, or record nothing.
POSSESSION = (
    "gecertificeerd", "certificeringen", "ons certificaat", "onze certificaten",
    "beschikt over", "beschikken over", "wij zijn", "we zijn", "is certified",
    "are certified", "our certification", "certificaat", "gecertificeerde",
    "voldoen aan de norm", "jaarlijks geaudit", "audit", "verklaring",
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": C.USER_AGENT,
                        "Accept-Language": "nl,en;q=0.8"})


@dataclass
class Evidence:
    company: str
    key: str
    url: str
    quote: str


def _text(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(txt.split())


def _sentence_around(text: str, start: int, end: int, width: int = 170) -> str:
    """Trim the window back to sentence edges so the quote reads as a claim,
    not as a slice through the middle of two unrelated ones."""
    lo = max(0, start - width)
    hi = min(len(text), end + width)
    window = text[lo:hi]
    off = start - lo
    left = max(window.rfind(". ", 0, off), window.rfind("! ", 0, off),
               window.rfind("? ", 0, off))
    if left != -1 and off - left < width:
        window = window[left + 2:]
        off -= left + 2
    right = window.find(". ", off)
    if right != -1:
        window = window[:right + 1]
    return window.strip()


LINK_HINTS = ("certific", "keurmerk", "kwaliteit", "iso", "nen", "security",
              "informatiebeveiliging", "beveiliging", "compliance", "over-ons",
              "about", "trust", "privacy-en-security", "organisatie")


def discover_links(base: str, html: str, limit: int = 8) -> list[str]:
    """Guessed paths mostly 404 on these sites. Following links the homepage
    actually exposes is what finds the certification page."""
    host = urlparse(base).netloc
    out, seen = [], set()
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>',
                         html, re.S | re.I):
        href, label = m.group(1), _text(m.group(2)).lower()
        url = urljoin(base, href)
        if urlparse(url).netloc != host or url in seen:
            continue
        haystack = (href + " " + label).lower()
        if any(h in haystack for h in LINK_HINTS):
            seen.add(url)
            out.append(url)
        if len(out) >= limit:
            break
    return out


def scan_site(company: str, base: str, paths: Iterable[str] = CANDIDATE_PATHS,
              budget: int = 9) -> list[Evidence]:
    found: dict[str, Evidence] = {}
    tried = 0
    queue: list[str] = [base]
    discovered = False
    while queue:
        if tried >= budget or len(found) >= len(CERT_PATTERNS):
            break
        url = queue.pop(0)
        try:
            r = SESSION.get(url, timeout=20, allow_redirects=True)
            tried += 1
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
        except requests.RequestException:
            tried += 1
            continue
        if not discovered:
            queue.extend(discover_links(r.url, r.text))
            queue.extend(urljoin(base, p) for p in paths if p)
            discovered = True
        text = _text(r.text)
        low = text.lower()
        for key, pat in CERT_PATTERNS.items():
            if key in found:
                continue
            m = re.search(pat, low)
            if not m:
                continue
            quote = _sentence_around(text, m.start(), m.end())
            ql = quote.lower()
            if any(n in ql for n in NEGATIVE):
                continue
            # Test possession over a wider context than we quote: a
            # "Erkend en gecertificeerd" heading can sit a paragraph above the
            # list of norms it introduces.
            ctx = low[max(0, m.start() - 450):m.end() + 450]
            if not any(v in ctx for v in POSSESSION):
                continue
            found[key] = Evidence(company, key, r.url, quote)
        time.sleep(0.3)
    return list(found.values())


def guess_homepage(name: str) -> Optional[str]:
    """Only tried when no homepage is supplied. A wrong guess produces no
    evidence rather than a wrong fact, because we still have to read the page."""
    slug = re.sub(r"[^a-z0-9]+", "", name.lower().split(" b.v")[0].split(" n.v")[0])
    if not slug or len(slug) < 3:
        return None
    for tld in (".nl", ".com"):
        url = f"https://www.{slug}{tld}"
        try:
            r = SESSION.head(url, timeout=12, allow_redirects=True)
            if r.status_code < 400:
                return url
        except requests.RequestException:
            continue
    return None


FIELDS = ["company_slug", "company_name", "req_type", "key", "value_num",
          "value_text", "trust", "source_url", "source_quote", "verified_by"]


def main(limit: int = 14) -> int:
    partners = json.loads((C.DATA / "partners.json").read_text())[:limit]
    homepages = {}
    hp_file = C.CURATED / "partner_homepages.json"
    if hp_file.exists():
        homepages = json.loads(hp_file.read_text())

    rows, misses = [], []
    for p in partners:
        base = homepages.get(p["slug"]) or guess_homepage(p["name"])
        if not base:
            misses.append((p["name"], "no homepage found"))
            continue
        homepages.setdefault(p["slug"], base)
        ev = scan_site(p["name"], base)
        if not ev:
            misses.append((p["name"], f"no certification text at {base}"))
        for e in ev:
            rows.append({
                "company_slug": p["slug"], "company_name": p["name"],
                "req_type": "certification", "key": e.key,
                "value_num": "", "value_text": e.key.upper().replace("_", " "),
                "trust": "claim", "source_url": e.url,
                "source_quote": e.quote[:400], "verified_by": "website-scan",
            })
        print(f"  {p['name'][:38]:38s} {base[:38]:38s} -> {[e.key for e in ev]}")

    hp_file.write_text(json.dumps(homepages, indent=2))
    out = C.CURATED / "partner_facts.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} evidenced facts -> {out}")
    if misses:
        print("needs a human (left blank rather than guessed):")
        for name, why in misses:
            print(f"  - {name}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
