"""Cache for PUBLIC price facts only. Bill data is never written here.

Key: vehicle id + job + part number + grade. Entries older than the price
freshness limit are ignored, and cached facts are marked from_cache=True so
the card can say so.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date

from .config import settings
from .schemas import Fact

_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(settings.cache_db)
    con.execute("CREATE TABLE IF NOT EXISTS price_facts (key TEXT, checked_on TEXT, fact TEXT)")
    return con


def price_key(vehicle_id: str, job: str, part_number: str | None, grade: str) -> str:
    return "|".join([vehicle_id, job, (part_number or "").upper(), grade])


def get_prices(key: str) -> list[Fact] | None:
    with _lock, _db() as con:
        rows = con.execute("SELECT checked_on, fact FROM price_facts WHERE key = ?", (key,)).fetchall()
    if not rows:
        return None
    today = date.today()
    facts = []
    for checked, blob in rows:
        if (today - date.fromisoformat(checked)).days <= settings.price_max_age_days:
            facts.append(Fact.model_validate(json.loads(blob)).model_copy(update={"from_cache": True}))
    return facts or None


def put_prices(key: str, facts: list[Fact]) -> None:
    with _lock, _db() as con:
        con.execute("DELETE FROM price_facts WHERE key = ?", (key,))
        con.executemany("INSERT INTO price_facts VALUES (?, ?, ?)",
                        [(key, (f.checked_on or date.today()).isoformat(), f.model_dump_json()) for f in facts])
