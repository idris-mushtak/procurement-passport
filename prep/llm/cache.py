"""Content-addressed SQLite cache.

Keyed on sha256(model + task + payload), so an identical call is free on the
second run. That matters more than it sounds: extraction and judgment get
re-run every time a rule or a threshold changes, and without this each rerun
pays the full bill again.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from typing import Any, Optional

from prep.llm import config as C

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        C.CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(C.CACHE_DB, check_same_thread=False)
        _conn.execute("""create table if not exists cache(
            key text primary key, model text, task text,
            value text not null, created_at real default (julianday('now')))""")
        _conn.commit()
    return _conn


def key_for(model: str, task: str, payload: Any) -> str:
    blob = json.dumps({"model": model, "task": task, "payload": payload},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get(key: str) -> Optional[Any]:
    with _lock:
        row = _db().execute("select value from cache where key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(key: str, model: str, task: str, value: Any) -> None:
    with _lock:
        _db().execute(
            "insert or replace into cache(key, model, task, value) values(?,?,?,?)",
            (key, model, task, json.dumps(value, ensure_ascii=False, default=str)))
        _db().commit()


def stats() -> dict:
    with _lock:
        n = _db().execute("select count(*) from cache").fetchone()[0]
    return {"entries": n, "path": str(C.CACHE_DB)}
