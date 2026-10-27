"""Collect CANDIDATE facts for a vehicle's fact pack. A human must verify them.

  python scripts/build_fact_pack.py honda_activa "Honda Activa 6G"

Uses Tavily to find official pages (owner's manual / service schedule,
warranty, recalls), reads the full page text with the text model, and writes
candidate facts with verbatim excerpts to facts/candidates/<vehicle_id>.json.

Nothing is marked verified. To use a fact: open its URL, confirm the excerpt
and numbers yourself, set source_rank (1 only for manufacturer or SIAM pages)
and verified_by_human, copy it into facts/<vehicle_id>.json, and when the
pack is complete set "status": "verified".
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.config import settings  # noqa: E402
from app.jobs import JOBS  # noqa: E402
from app.llm import ModelError, call_json, untrusted  # noqa: E402

QUERIES = {
    "schedule": ["{name} periodic maintenance schedule owner's manual", "{name} service schedule air filter spark plug km"],
    "warranty": ["{name} warranty years km official"],
    "recall": ["{name} recall SIAM", "{name} recall manufacturing period"],
}


class _Cand(BaseModel):
    kind: str
    data: dict
    excerpt: str


class _Out(BaseModel):
    facts: list[_Cand] = []


def search(q: str) -> list[dict]:
    with httpx.Client(timeout=20) as c:
        r = c.post("https://api.tavily.com/search",
                   json={"query": q, "search_depth": "advanced", "max_results": 5, "include_raw_content": True},
                   headers={"Authorization": f"Bearer {settings.tavily_api_key}"})
        r.raise_for_status()
        return r.json().get("results", [])


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    vid, name = sys.argv[1], sys.argv[2]
    if not (settings.tavily_api_key and settings.nebius_api_key):
        print("Set NEBIUS_API_KEY and TAVILY_API_KEY in .env first.")
        return 2
    jobs = ", ".join(j.id for j in JOBS if j.kind in ("maintenance", "add_on"))
    out: list[dict] = []
    for kind, qs in QUERIES.items():
        for q in qs:
            for res in search(q.format(name=name)):
                text, url = res.get("raw_content") or "", res.get("url") or ""
                if len(text) < 300:
                    continue
                msgs = [
                    {"role": "system", "content":
                        f"Extract {kind} facts for {name} from the page. Return JSON {{\"facts\": [{{\"kind\": \"{kind}\", "
                        "\"data\": {...}, \"excerpt\": \"verbatim sentence from the page\"}]}}. "
                        "schedule data: {job (one of: " + jobs + "), interval_km, interval_months, "
                        "condition (whichever_first|whichever_later|km_only|months_only), action (replace|inspect)}. "
                        "warranty data: {years, km, starts_from (purchase|registration), excluded_job_kinds}. "
                        "recall data: {affected_jobs, mfg_from YYYY-MM, mfg_to YYYY-MM, variants, defect}. "
                        "Only facts stated on the page; excerpt must be copied exactly. Empty list if none."},
                    {"role": "user", "content": f"URL: {url}\n" + untrusted("WEB PAGE", text[:12000])},
                ]
                try:
                    got = call_json("factpack", settings.model_text, msgs, _Out, max_tokens=3000)
                except ModelError as exc:
                    print("skip", url, exc.kind)
                    continue
                page = " ".join(text.split()).lower()
                for f in got.facts:
                    if " ".join(f.excerpt.split()).lower() not in page:
                        continue  # excerpt not on page: discard
                    out.append({"kind": f.kind, "data": f.data, "source_url": url, "excerpt": f.excerpt,
                                "checked_on": date.today().isoformat(), "source_rank": None,
                                "verified_by_human": False, "applies_to": {}})
                print(f"{kind}: {url} -> {len(got.facts)} candidate(s)")
    dest = ROOT / "facts" / "candidates" / f"{vid}.json"
    dest.write_text(json.dumps({"vehicle_id": vid, "name": name, "candidates": out}, indent=2, ensure_ascii=False))
    print(f"\n{len(out)} candidates written to {dest}. Verify each against its page before copying it into the pack.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
