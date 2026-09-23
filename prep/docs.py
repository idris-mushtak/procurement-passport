"""Turn a document pack into the handful of pages that state the requirements.

A Dutch leidraad runs 40-80 pages; the geschiktheidseisen live on three or four
of them. Sending the whole document to the model is slower, costlier and
measurably worse -- it invents requirements out of contract boilerplate. So we
score pages on Dutch selection-criteria vocabulary and send only the top ones,
each tagged with its real page number so every extracted row can cite it.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from prep import config as C

# Weighted: a page saying "geschiktheidseisen" is the page we want; a page that
# merely says "combinatie" is probably the contract terms.
WEIGHTS = {
    "geschiktheidseis": 6.0, "minimumeis": 4.0, "kerncompetentie": 4.0,
    "beroepsbekwaamheid": 4.0, "draagkracht": 3.5, "ervaringseis": 3.5,
    "uitsluitingsgrond": 3.0, "referentie": 2.5, "omzet": 2.5,
    "verzeker": 2.5, "certifica": 2.0, "iso 27001": 2.0, "iso 9001": 2.0,
    "nen 7510": 2.0, "onderaannem": 1.5, "combinatie": 1.5,
    "aansprakelijkheid": 1.5, "beroepsaansprakelijk": 2.0,
}
TOC_MARKER = re.compile(r"\.{6,}\s*\d+\s*$", re.M)  # ".......... 23" table-of-contents rows


@dataclass
class Page:
    doc_name: str
    page: int
    text: str
    score: float


def _pdf_pages(path: Path) -> list[tuple[int, str]]:
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            out.append((i, p.extract_text() or ""))
    return out


def _docx_pages(path: Path) -> list[tuple[int, str]]:
    """docx has no pages. Chunk paragraphs into ~3000-char pseudo-pages so the
    citation still points somewhere a human can find."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError):
        return []
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = re.sub(r"\n{3,}", "\n\n", text)
    chunks, buf, n = [], [], 1
    for para in text.split("\n"):
        buf.append(para)
        if sum(len(x) for x in buf) > 3000:
            chunks.append((n, "\n".join(buf)))
            buf, n = [], n + 1
    if buf:
        chunks.append((n, "\n".join(buf)))
    return chunks


def score_page(text: str) -> float:
    low = text.lower()
    if not low.strip():
        return 0.0
    s = sum(w for k, w in WEIGHTS.items() if k in low)
    # A contents page matches every keyword and contains no requirement.
    if len(TOC_MARKER.findall(text)) >= 4:
        s *= 0.15
    # Long prose beats a heading-only page.
    if len(low) < 400:
        s *= 0.5
    return s


def pages_for(pack_dir: Path, limit: int = C.MAX_PAGES_TO_MODEL) -> list[Page]:
    pages: list[Page] = []
    for path in sorted(pack_dir.glob("*")):
        if path.suffix.lower() == ".pdf":
            raw = _pdf_pages(path)
        elif path.suffix.lower() in (".docx", ".doc"):
            raw = _docx_pages(path)
        else:
            continue
        for num, text in raw:
            sc = score_page(text)
            if sc > 0:
                pages.append(Page(path.name, num, text, sc))
    pages.sort(key=lambda p: -p.score)
    top = [p for p in pages if p.score >= 3.0][:limit]
    # Keep reading order so the model sees clauses in sequence, not by rank.
    top.sort(key=lambda p: (p.doc_name, p.page))
    return top


def as_prompt_block(pages: list[Page]) -> str:
    parts = []
    for p in pages:
        body = re.sub(r"\n{3,}", "\n\n", p.text).strip()
        parts.append(f"<<<DOC {p.doc_name} | PAGE {p.page}>>>\n{body}")
    return "\n\n".join(parts)
