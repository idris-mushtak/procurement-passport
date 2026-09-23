"""Requirement extraction: deterministic patterns, the model, or both.

Split of labour:
  * `rules`  catches stereotyped numeric thresholds. Right or silent.
  * `llm`    catches prose requirements the patterns cannot reach -- staffing,
             local presence, kerncompetenties written as paragraphs.
  * `both`   runs the patterns first and lets the model add what they missed.
             On a conflict the pattern wins for numbers (it read the digits)
             and the model wins for wording.

Nothing here is trusted blindly: every row is Pydantic-validated, every row
carries the clause it came from, and `data/curated/overrides.json` lets a human
correct or delete a row without editing code. Demo correctness beats automation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import ValidationError

from prep import config as C
from prep import llm, rules_extract
from prep.llm.ledger import LEDGER, Call
from prep.docs import Page, as_prompt_block, pages_for
from prep.models import ExtractionBatch, Requirement

Backend = Literal["rules", "llm", "both"]

SYSTEM = """Je bent een Nederlandse aanbestedingsanalist.
Je leest fragmenten uit een aanbestedingsleidraad en haalt daar UITSLUITEND de
knock-out geschiktheidseisen uit: eisen waaraan een inschrijver moet voldoen om
niet te worden uitgesloten.

Regels:
- Geef alleen eisen terug die letterlijk in de tekst staan. Verzin niets.
- Elke eis MOET een `source_quote` hebben: een letterlijk citaat uit de tekst
  (maximaal 300 tekens) en het `page` nummer uit de <<<DOC ... | PAGE n>>> kop.
- Gunningscriteria, wensen, pre's en contractvoorwaarden zijn GEEN eisen. Sla ze over.
- Een eis die een subjectief oordeel vraagt ("vergelijkbaar", "passend",
  "naar tevredenheid") krijg req_type "other". Zet die nooit om in een getal.
- Bedragen als heel getal in euro (1.250.000 -> 1250000).

Antwoord met JSON:
{"requirements": [{
  "req_type": "turnover|certification|references|insurance|staff_language|local_presence|other",
  "key": "turnover_avg3y|iso_27001|iso_9001|nen_7510|liability_insurance_eur|
          professional_indemnity_eur|reference_count|reference_min_value_eur|
          lang_nl|fte|office_nl|<eigen slug>",
  "operator": "gte|lte|eq|exists|count_gte",
  "threshold_num": <getal of null>,
  "threshold_text": "<tekst of null>",
  "window_years": <getal of null>,
  "public_sector_required": true|false,
  "knockout": true|false,
  "lot": "<perceel of null>",
  "clause_ref": "<paragraafnummer of null>",
  "source_quote": "<letterlijk citaat>",
  "page": <paginanummer>,
  "confidence": "high|medium|low"
 }],
 "joint_bids_allowed": true|false|null,
 "subcontracting_allowed": true|false|null}
"""


def _validate(tender_id: str, payload: dict) -> tuple[list[Requirement], dict]:
    reqs, dropped = [], 0
    for raw in payload.get("requirements", []) or []:
        raw = dict(raw)
        raw["tender_id"] = tender_id
        raw.setdefault("confidence", "medium")
        try:
            reqs.append(Requirement(**raw))
        except ValidationError:
            dropped += 1
    flags = {
        k: payload.get(k)
        for k in ("joint_bids_allowed", "subcontracting_allowed")
        if isinstance(payload.get(k), bool)
    }
    return reqs, {"flags": flags, "dropped": dropped}


def _cache_path(tender_id: str, block: str):
    import hashlib
    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()[:16]
    return C.CACHE / f"llm_{tender_id}_{digest}.json"


def extract_llm(tender_id: str, pages: list[Page],
                use_cache: bool = True) -> tuple[list[Requirement], dict]:
    """Fast model first; escalate once to the strong model if the response does
    not survive validation. A malformed row is dropped, never patched.

    Responses are cached against a hash of the exact prompt, so re-running after
    a rules or override change costs nothing for tenders that did not move --
    which matters when the whole build is three hours long.
    """
    block = as_prompt_block(pages)
    cache = _cache_path(tender_id, block)
    if use_cache and cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        reqs, info = _validate(tender_id, payload["response"])
        # Record the hit at zero cost. Skipping it entirely would make a cached
        # run look like no work happened, and hide how much the cache saves.
        LEDGER.record(Call(task="extract.tender", tier="SMART", provider="nebius",
                           model=payload.get("model") or "?", cache_hit=True))
        meta = {"model": payload.get("model"), "cached": True}
        meta.update(info)
        return reqs, meta
    user = (f"Aanbesteding {tender_id}. Fragmenten uit de aanbestedingsstukken:\n\n"
            f"{block}\n\nGeef de geschiktheidseisen als JSON.")
    meta: dict = {"model": None, "escalated": False}
    for model in (C.MODEL_FAST, C.MODEL_STRONG):
        comp = llm.complete(SYSTEM, user, model, task="extract.tender")
        meta["model"] = comp.model
        meta["tokens"] = comp.prompt_tokens + comp.completion_tokens
        payload = llm.parse_json(comp.text)
        if payload is None:
            meta["escalated"] = True
            continue
        reqs, info = _validate(tender_id, payload)
        meta.update(info)
        if reqs:
            cache.write_text(json.dumps({"model": comp.model, "response": payload},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
            return reqs, meta
        meta["escalated"] = True
    return [], meta


def _merge(rule_rows: list[Requirement], llm_rows: list[Requirement]) -> list[Requirement]:
    by_key: dict[tuple[str, str], Requirement] = {}
    for r in rule_rows:
        by_key[(r.req_type, r.key)] = r
    for r in llm_rows:
        k = (r.req_type, r.key)
        cur = by_key.get(k)
        if cur is None:
            by_key[k] = r
        elif cur.threshold_num is None and r.threshold_num is not None:
            by_key[k] = r
        # else: the pattern already read the digits off the page -- keep it.
    return list(by_key.values())


# --- hand corrections ------------------------------------------------------
OVERRIDES = C.CURATED / "overrides.json"


def load_overrides() -> dict:
    if not OVERRIDES.exists():
        return {}
    return json.loads(OVERRIDES.read_text(encoding="utf-8"))


def apply_overrides(tender_id: str, reqs: list[Requirement]) -> list[Requirement]:
    """`drop` removes a bad row by key; `add` inserts a hand-read clause;
    `set` patches fields on a row the extractor got nearly right."""
    ov = load_overrides().get(tender_id)
    if not ov:
        return reqs
    out = [r for r in reqs if r.key not in set(ov.get("drop", []))]
    for key, patch in (ov.get("set") or {}).items():
        for r in out:
            if r.key == key:
                for f, v in patch.items():
                    setattr(r, f, v)
    for raw in ov.get("add", []) or []:
        raw = dict(raw)
        raw["tender_id"] = tender_id
        raw.setdefault("confidence", "high")
        try:
            row = Requirement(**raw)
        except ValidationError as exc:
            print(f"  ! override for {tender_id} is invalid: {exc.errors()[0]['msg']}")
            continue
        # A hand-added row REPLACES the extracted row for the same requirement.
        # Appending instead would show the reviewer's clause and the extractor's
        # clause side by side as two separate requirements.
        out = [r for r in out if (r.req_type, r.key) != (row.req_type, row.key)]
        out.append(row)
    return out


def extract_tender(tender_id: str, backend: Backend = "both") -> tuple[list[Requirement], dict]:
    pages = pages_for(C.PACKS / tender_id)
    meta: dict = {"pages": len(pages), "backend": backend}
    if not pages:
        meta["note"] = "no document pack -- TED notice text only"
        return [], meta

    rule_rows = rules_extract.extract(tender_id, pages)
    flags = rules_extract.flags(pages)
    rows = rule_rows

    if backend in ("llm", "both"):
        try:
            llm_rows, lmeta = extract_llm(tender_id, pages)
            meta.update(lmeta)
            rows = llm_rows if backend == "llm" else _merge(rule_rows, llm_rows)
            # The model may only ADD a restriction, never remove one.
            #
            # Asked whether combinations and subcontracting are allowed, the
            # model answers `true` whenever the pages it was given do not
            # discuss the question -- which is most of the time. Letting that
            # override the pattern detector flipped all twelve tenders to
            # "both allowed", and those flags decide whether a partner can
            # close a gap at all. A wrong `true` makes the product promise a
            # partner who is not permitted to bid; a wrong `false` only costs
            # us a recommendation. So False wins, from whichever side it came.
            for k, v in (lmeta.get("flags") or {}).items():
                if v is False:
                    flags[k] = False
        except llm.LLMUnavailable as exc:
            if backend == "llm":
                raise
            meta["llm"] = f"skipped ({exc.args[0].splitlines()[0]})"
        except Exception as exc:
            # A timeout or a 5xx on one tender must not cost us the other
            # eleven. We still have that tender's pattern rows.
            if backend == "llm":
                raise
            meta["llm"] = f"failed ({type(exc).__name__}) -- kept pattern rows"

    rows = apply_overrides(tender_id, rows)
    meta["flags"] = flags
    meta["rules_rows"] = len(rule_rows)
    meta["final_rows"] = len(rows)
    return rows, meta
