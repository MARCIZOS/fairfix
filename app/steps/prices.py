"""Steps 5-6 - Price facts: Tavily finds listings, the text model reads them.

- Search is limited to the credible-seller domains in config.
- The model reads the FULL page text (Tavily raw content), never a snippet,
  and must copy a verbatim excerpt containing the price. Facts whose excerpt
  isn't found in the page, or whose price isn't in the excerpt, are dropped.
- The model only extracts listings; rules.check_price decides everything.
- Non-blocking: any failure returns [] plus a warning, and the price check
  then says "Unable to verify price fairly".
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, field_validator

from .. import cache
from ..config import CREDIBLE_SELLER_DOMAINS, settings
from ..jobs import get_job
from ..llm import ModelError, call_json, untrusted
from ..schemas import Fact, LineItem, Vehicle
from .extract import _num

TAVILY_URL = "https://api.tavily.com/search"


class _Listing(BaseModel):
    part_name: str = ""
    part_number: str | None = None
    grade: Literal["genuine", "oem", "aftermarket", "unknown"] = "unknown"
    price_inr: float | None = None
    gst_included: bool | None = None
    gst_rate_pct: float | None = None
    listing_type: Literal["part_only", "installed", "unknown"] = "unknown"
    in_stock: bool | None = None
    excerpt: str = ""

    _n = field_validator("price_inr", "gst_rate_pct", mode="before")(lambda cls, v: _num(v))

    @field_validator("grade", "listing_type", mode="before")
    @classmethod
    def _lower(cls, v):
        return (v or "unknown").lower() if isinstance(v, str) else "unknown"


class _Out(BaseModel):
    listings: list[_Listing] = []


def _squash(s: str) -> str:
    return " ".join((s or "").split()).lower()


def tavily_search(query: str, timeout: float | None = None) -> list[dict]:
    if not settings.search_configured:
        return []
    body = {"query": query, "search_depth": settings.search_depth, "max_results": 5, "include_raw_content": True,
            "include_domains": list(CREDIBLE_SELLER_DOMAINS)}
    headers = {"Authorization": f"Bearer {settings.tavily_api_key}"}
    with httpx.Client(timeout=timeout or settings.search_timeout_s) as c:
        r = c.post(TAVILY_URL, json=body, headers=headers)
        r.raise_for_status()
        return r.json().get("results", [])


def queries_for(line: LineItem, vehicle: Vehicle) -> list[str]:
    job = get_job(line.job or "")
    label = job.label if job else (line.raw_text.value or "")
    base = f"{vehicle.make} {vehicle.model} {vehicle.variant or ''}".strip()
    qs = []
    if line.part_number.value:
        qs.append(f"{line.part_number.value} {base} price")
    grade = "genuine" if line.grade in ("genuine", "oem") else ""
    qs.append(f"{base} {label.replace(' replacement', '')} {grade} price".replace("  ", " "))
    return qs[:2]


def extract_listings(page_text: str, url: str, line: LineItem, vehicle: Vehicle) -> list[Fact]:
    job = get_job(line.job or "")
    target = f"{job.label if job else ''} for {vehicle.make} {vehicle.model} {vehicle.variant or ''}".strip()
    msgs = [
        {"role": "system", "content":
            "You extract product listings from an e-commerce page. Return JSON "
            "{\"listings\": [{\"part_name\", \"part_number\", \"grade\" (genuine|oem|aftermarket|unknown), "
            "\"price_inr\", \"gst_included\" (true|false|null), \"gst_rate_pct\", \"listing_type\" (part_only|installed|unknown), "
            "\"in_stock\" (true|false|null), \"excerpt\"}]}. Only include listings for the target part and vehicle. "
            "excerpt must be copied VERBATIM from the page and contain the price. Use null/unknown for anything not stated. "
            "Return {\"listings\": []} if none match."},
        {"role": "user", "content": f"Target part: {target}\nPage URL: {url}\n" + untrusted("WEB PAGE", page_text[:9000])},
    ]
    out = call_json("price_extract", settings.model_text, msgs, _Out, max_tokens=2000)
    page = _squash(page_text)
    facts: list[Fact] = []
    for i, l in enumerate(out.listings):
        ex = _squash(l.excerpt)
        if not ex or ex not in page or l.price_inr is None:
            continue
        p = f"{l.price_inr:.2f}"
        digits = re.sub(r"\D", "", p[:-3] if p.endswith(".00") else p)
        if digits and digits not in re.sub(r"\D", "", ex):
            continue
        facts.append(Fact(
            kind="part_price", source_url=url + (f"#listing-{i}" if i else ""), excerpt=l.excerpt.strip()[:400],
            checked_on=date.today(), source_rank=2,
            data={"job": line.job, "part_name": l.part_name, "part_number": l.part_number, "grade": l.grade,
                  "price_inr": l.price_inr, "gst_included": l.gst_included, "gst_rate_pct": l.gst_rate_pct,
                  "listing_type": l.listing_type, "in_stock": l.in_stock, "delivers_to_city": None},
        ))
    return facts


MAX_PAGES_PER_PART = 6


def lookup_comparables(line: LineItem, vehicle: Vehicle) -> tuple[list[Fact], list[str]]:
    """Return (comparables, warnings). Never raises."""
    if line.kind in ("labour", "part_with_fitting") or not line.job:
        return [], []
    key = cache.price_key(vehicle.vehicle_id or "unknown", line.job, line.part_number.value, line.grade)
    try:
        cached = cache.get_prices(key)
    except Exception:
        cached = None
    if cached:
        return cached, []
    if not (settings.search_configured and settings.models_configured):
        return [], ["Price search not configured; price checks show Unable to verify."]
    facts: list[Fact] = []
    warnings: list[str] = []
    # Both searches run at once, then every result page is read at once (was one by one: ~17 s for 3 parts).
    pages: list[tuple[float, str, str]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        searches = [pool.submit(tavily_search, q) for q in queries_for(line, vehicle)]
        seen: set[str] = set()
        for fut in searches:
            try:
                for res in fut.result():
                    text, url = res.get("raw_content") or "", res.get("url") or ""
                    if len(text) >= 200 and url and url not in seen:
                        seen.add(url)
                        pages.append((float(res.get("score") or 0), text, url))
            except httpx.HTTPError as exc:
                warnings.append(f"Price search failed ({type(exc).__name__}); price checks show Unable to verify.")
        pages.sort(key=lambda p: -p[0])   # read the most relevant pages only: each read is a model call
        reads = [pool.submit(extract_listings, text, url, line, vehicle) for _, text, url in pages[:MAX_PAGES_PER_PART]]
        for fut in reads:
            try:
                facts.extend(fut.result())
            except ModelError as exc:
                warnings.append(f"A price page couldn't be read ({exc.kind}).")
            except Exception as exc:  # one bad page never sinks the rest
                warnings.append(f"A price page couldn't be read ({type(exc).__name__}).")
    if facts:
        try:
            cache.put_prices(key, facts)
        except Exception:
            pass
    return facts, warnings
