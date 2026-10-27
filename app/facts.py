"""Fact packs: hand-checked reference facts for each supported vehicle.

A pack is a JSON file in facts/. Only packs with "status": "verified" are
treated as supported; a "template" pack gives a partial audit, so the app
never presents unchecked facts as evidence.

Which facts are *sufficient* to decide a result is defined here, in one
place, per check:
- schedule / warranty / recall / labour_price: source_rank 1 (manufacturer
  or official portal such as SIAM), verified_by_human, with a URL and the
  exact excerpt. Anything else is secondary: it can only point the user to
  official verification.
- part_price: decided in rules.check_price (needs several equivalent,
  credible, fresh listings), never by a single fact.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field as PField

from .config import settings
from .schemas import Fact, Vehicle


class FactPack(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vehicle_id: str
    display_name: str
    make: str
    model: str
    variants: list[str] = PField(default_factory=list)
    fuels: list[str] = PField(default_factory=list)
    status: str = "template"                  # "template" | "verified"
    fictional: bool = False                   # True only for the clearly-labelled sample vehicle
    verified_by: str = ""
    verified_on: date | None = None
    schedule_complete: bool = False           # True only if the whole official schedule was transcribed
    recall_sources_checked: list[dict[str, Any]] = PField(default_factory=list)
    recall_lookup_url: str = ""               # manufacturer's own VIN/chassis lookup, from a verified source
    notes: str = ""
    facts: list[Fact] = PField(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.status == "verified"

    # ------------------------------------------------------------------
    def _applies(self, fact: Fact, vehicle: Vehicle) -> tuple[bool, str | None]:
        """Does this fact apply to this vehicle? Returns (applies, reason_if_unknown)."""
        a = fact.applies_to or {}
        variants = a.get("variants")
        if variants:
            if not vehicle.variant:
                return False, "the fact is limited to certain variants and the variant isn't known"
            if vehicle.variant.lower() not in {v.lower() for v in variants}:
                return False, None
        fuels = a.get("fuels")
        if fuels:
            if not vehicle.fuel:
                return False, "the fact is limited to certain fuel types and the fuel type isn't known"
            if vehicle.fuel.lower() not in {f.lower() for f in fuels}:
                return False, None
        if "severe_use" in a and bool(a["severe_use"]) != bool(vehicle.severe_use):
            return False, None
        return True, None

    def facts_for(self, kind: str, vehicle: Vehicle, job: str | None = None) -> tuple[list[Fact], list[Fact], list[str]]:
        """Split matching facts into (sufficient, secondary, unknown_reasons)."""
        sufficient: list[Fact] = []
        secondary: list[Fact] = []
        reasons: list[str] = []
        for f in self.facts:
            if f.kind != kind:
                continue
            if job is not None and f.data.get("job") not in (None, job):
                if kind != "recall":
                    continue
            ok, why = self._applies(f, vehicle)
            if not ok:
                if why:
                    reasons.append(why)
                continue
            f = f.model_copy(update={"sufficient": is_sufficient(f, self)})
            (sufficient if f.sufficient else secondary).append(f)
        return sufficient, secondary, reasons


def is_sufficient(fact: Fact, pack: FactPack | None = None) -> bool:
    if pack is not None and not pack.supported:
        return False
    return bool(
        fact.source_rank == 1
        and fact.verified_by_human
        and fact.source_url
        and fact.excerpt.strip()
    )


@lru_cache(maxsize=64)
def _load(path: str, mtime: float) -> FactPack:  # mtime busts the cache when a file changes
    return FactPack.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def list_packs(include_fictional: bool | None = None) -> list[FactPack]:
    include_fictional = settings.allow_fictional_sample if include_fictional is None else include_fictional
    packs: list[FactPack] = []
    for p in sorted(settings.facts_dir.glob("*.json")):
        pack = _load(str(p), p.stat().st_mtime)
        if pack.fictional and not include_fictional:
            continue
        packs.append(pack)
    return packs


def get_pack(vehicle_id: str | None) -> FactPack | None:
    if not vehicle_id:
        return None
    for pack in list_packs():
        if pack.vehicle_id == vehicle_id:
            return pack
    return None
