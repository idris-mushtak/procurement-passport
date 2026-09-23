"""Download TED notice XML for every tender in the CSV.

TED serves eForms UBL XML at /en/notice/<id>/xml with no auth. That XML carries
the coded exclusion grounds and selection criteria, which is what the gap engine
needs -- far more reliable than scraping the TenderNed document pack.
"""
import csv, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "ted_dutch_it_tenders.csv"
OUT = ROOT / "data" / "notices"
URL = "https://ted.europa.eu/en/notice/{}/xml"
HEADERS = {"User-Agent": "procurement-passport-prep/0.1"}


def fetch(ted_id: str) -> tuple[str, str]:
    dest = OUT / f"{ted_id}.xml"
    if dest.exists() and dest.stat().st_size > 2000:
        return ted_id, "cached"
    for attempt in range(3):
        try:
            r = requests.get(URL.format(ted_id), headers=HEADERS, timeout=40)
            if r.status_code == 200 and len(r.content) > 2000:
                dest.write_bytes(r.content)
                return ted_id, "ok"
            last = f"http {r.status_code} len {len(r.content)}"
        except requests.RequestException as exc:
            last = type(exc).__name__
        time.sleep(1.5 * (attempt + 1))
    return ted_id, f"FAIL {last}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ids = [r["ted_id"] for r in csv.DictReader(CSV.open(encoding="utf-8")) if r["ted_id"]]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(fetch, ids))
    bad = [f"{i}: {s}" for i, s in results if s.startswith("FAIL")]
    ok = sum(1 for _, s in results if s in ("ok", "cached"))
    print(f"{ok}/{len(ids)} notices available in {OUT}")
    for b in bad:
        print("  ", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
