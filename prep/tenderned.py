"""Download the TenderNed document pack for a tender.

TenderNed exposes a public JSON API (no auth) behind its Angular front end.
`https://www.tenderned.nl/aankondigingen/overzicht/<publicatieId>` maps to
`/papi/tenderned-rs-tns/v2/publicaties/<publicatieId>`, and its `/documenten`
sibling lists every published file with a direct download href.

We do not pull the whole pack. Contract annexes, price sheets and the GIBIT
boilerplate never carry selection criteria; opening them just burns tokens.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from prep import config as C

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": C.USER_AGENT, "Accept": "application/json"})


@dataclass
class PackDoc:
    doc_id: str
    name: str
    ext: str
    size: int
    category: str
    url: str
    path: Optional[Path] = None


def publicatie_id(documents_url: str) -> Optional[str]:
    m = re.search(r"/overzicht/(\d+)", documents_url or "")
    return m.group(1) if m else None


def _get(url: str, **kw) -> requests.Response:
    last = None
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=C.HTTP_TIMEOUT, **kw)
            if r.status_code == 200:
                return r
            last = f"http {r.status_code}"
        except requests.RequestException as exc:
            last = type(exc).__name__
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def list_documents(pub_id: str) -> list[PackDoc]:
    r = _get(f"{C.TENDERNED_API}/publicaties/{pub_id}/documenten")
    docs = []
    for d in r.json().get("documenten", []):
        href = (d.get("links") or {}).get("download", {}).get("href")
        if not href:
            continue
        docs.append(PackDoc(
            doc_id=str(d.get("documentId")),
            name=d.get("documentNaam", ""),
            ext=(d.get("typeDocument") or {}).get("code", ""),
            size=int(d.get("grootte") or 0),
            category=(d.get("publicatieCategorie") or {}).get("code", ""),
            url="https://www.tenderned.nl" + href,
        ))
    return docs


def relevant(docs: list[PackDoc]) -> list[PackDoc]:
    """Rank by how likely the file is to contain geschiktheidseisen."""
    scored = []
    for d in docs:
        if d.ext not in ("pdf", "docx", "doc"):
            continue
        if d.size > C.MAX_PACK_BYTES or d.size < 5_000:
            continue
        low = d.name.lower()
        if any(k in low for k in C.DOC_NAME_EXCLUDE):
            continue
        score = 0.0
        if any(k in low for k in C.DOC_NAME_KEYWORDS):
            score += 3.0
        if d.category in ("DOC", "ANK"):
            score += 1.0
        if d.category == "NVI":
            score += 0.5      # errata can move a threshold; worth reading second
        if 50_000 <= d.size <= 4_000_000:
            score += 1.0      # a leidraad is rarely tiny and rarely a 12MB scan
        if score <= 0:
            continue
        scored.append((score, d))
    scored.sort(key=lambda t: (-t[0], -t[1].size))
    return [d for _, d in scored[:4]]


def download(pub_id: str, doc: PackDoc, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", doc.name)[:70]
    path = dest_dir / f"{doc.doc_id}_{safe}.{doc.ext}"
    if path.exists() and path.stat().st_size > 1000:
        doc.path = path
        return path
    r = _get(doc.url, headers={"Accept": "*/*"})
    path.write_bytes(r.content)
    doc.path = path
    return path


def fetch_pack(ted_id: str, documents_url: str) -> list[PackDoc]:
    pub_id = publicatie_id(documents_url)
    if not pub_id:
        return []
    dest = C.PACKS / ted_id
    docs = relevant(list_documents(pub_id))
    for d in docs:
        download(pub_id, d, dest)
    (dest / "manifest.json").write_text(json.dumps(
        [{k: (str(v) if k == "path" else v) for k, v in d.__dict__.items()} for d in docs],
        indent=2))
    return docs
