"""FastAPI app. Bill data lives only for the length of a request.

Endpoints
  GET  /health                 model / search configuration and reachability
  GET  /api/config             thresholds and policies shown in the UI
  GET  /api/vehicles           fact packs and whether each is supported
  GET  /api/facts/{vehicle}    a fact pack with sources, excerpts and dates
  GET  /api/jobs               the standard job catalogue
  POST /api/extract            photos -> Document(s) for the correction screen
  POST /api/audit              corrected documents -> AuditReport
  POST /api/message            redraft the advisor message (e.g. in Hindi)
  GET  /api/demo/{name}        a stored sample case run through the real rules
"""

from __future__ import annotations

import base64
import binascii
import json
import logging

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import facts as factmod
from .audit import run_audit
from .config import ROOT, settings
from .jobs import JOBS
from .llm import ModelError, list_models
from .rules import check_bill_arithmetic, check_line_arithmetic
from .schemas import ApprovalNote, AuditReport, Document, Vehicle
from .steps import message, normalise
from .steps.extract import extract_document

log = logging.getLogger("fairfix")
app = FastAPI(title="FairFix", version="0.1.0", docs_url="/api/docs")

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 6


@app.exception_handler(Exception)
async def _no_leak(request: Request, exc: Exception):
    # Log the error type only: never bill content, values or tracebacks with locals.
    log.error("unhandled %s on %s", type(exc).__name__, request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_error", "type": type(exc).__name__})


# ------------------------------------------------------------------ info


@app.get("/health")
def health():
    out = {"models_configured": settings.models_configured, "search_configured": settings.search_configured,
           "model_vision": settings.model_vision, "model_text": settings.model_text,
           "model_explain": settings.explain_model, "models_reachable": None}
    if settings.models_configured:
        try:
            ids = list_models()
            out["models_reachable"] = True
            out["configured_models_listed"] = {m: (m in ids) for m in {settings.model_vision, settings.model_text, settings.explain_model}}
        except ModelError as exc:
            out["models_reachable"] = False
            out["error"] = exc.kind
        except Exception as exc:
            out["models_reachable"] = False
            out["error"] = type(exc).__name__
    return out


@app.get("/api/config")
def config():
    return {
        "price_review_threshold_pct": settings.price_review_threshold_pct,
        "price_min_comparables": settings.price_min_comparables,
        "price_min_sellers": settings.price_min_sellers,
        "price_max_age_days": settings.price_max_age_days,
        "line_rounding_tolerance_inr": settings.line_rounding_tolerance_inr,
        "total_rounding_tolerance_inr": settings.total_rounding_tolerance_inr,
        "schedule_window_pct": settings.schedule_window_pct,
        "estimate_amount_tolerance_pct": settings.estimate_amount_tolerance_pct,
        "models_configured": settings.models_configured,
        "search_configured": settings.search_configured,
        "privacy": "Bill photos are sent to an AI model hosted by Nebius to be read. FairFix keeps nothing after "
                   "the response: no bills, extracted fields or results are stored or logged.",
    }


@app.get("/api/vehicles")
def vehicles():
    return [{"vehicle_id": p.vehicle_id, "display_name": p.display_name, "make": p.make, "model": p.model,
             "variants": p.variants, "fuels": p.fuels, "supported": p.supported, "fictional": p.fictional,
             "recall_lookup_url": p.recall_lookup_url or None}
            for p in factmod.list_packs()]


@app.get("/api/facts/{vehicle_id}")
def facts(vehicle_id: str):
    pack = factmod.get_pack(vehicle_id)
    if not pack:
        raise HTTPException(404, "unknown vehicle")
    return pack.model_dump(mode="json")


@app.get("/api/jobs")
def jobs():
    return [{"id": j.id, "label": j.label, "kind": j.kind} for j in JOBS]


# ------------------------------------------------------------------ extract


async def _read_pages(files: list[UploadFile] | None) -> list[tuple[bytes, str]]:
    pages = []
    for f in files or []:
        if not f.filename and not f.size:
            continue
        if f.content_type not in ALLOWED_MIME:
            raise HTTPException(415, f"Unsupported file type {f.content_type}; use JPEG, PNG or WebP photos.")
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Each photo must be under 10 MB.")
        pages.append((data, f.content_type))
    if len(pages) > MAX_PAGES:
        raise HTTPException(413, f"At most {MAX_PAGES} pages per document.")
    return pages


def _flags(doc: Document) -> list[dict]:
    out = [f.model_dump(mode="json") for f in check_bill_arithmetic(doc)]
    for i, ln in enumerate(doc.lines):
        f = check_line_arithmetic(ln)
        if f.category == "needs_confirmation":
            out.append({**f.model_dump(mode="json"), "line_index": i})
    return out


@app.post("/api/extract")
async def extract(bill: list[UploadFile] = File(...), estimate: list[UploadFile] | None = File(None)):
    if not settings.models_configured:
        raise HTTPException(503, "Reading photos needs NEBIUS_API_KEY. You can still try the sample on the home page.")
    bill_pages = await _read_pages(bill)
    est_pages = await _read_pages(estimate)
    if not bill_pages:
        raise HTTPException(400, "Add at least one photo of the bill.")
    try:
        bdoc = extract_document(bill_pages, "bill")
        edoc = extract_document(est_pages, "estimate") if est_pages else None
    except ModelError as exc:
        raise HTTPException(502, f"The bill couldn't be read ({exc.kind}). Try a sharper, flatter photo.")
    warnings = normalise.normalise(bdoc)
    if edoc:
        warnings += normalise.normalise(edoc)
    return {"bill": bdoc.model_dump(mode="json"), "estimate": edoc.model_dump(mode="json") if edoc else None,
            "flags": _flags(bdoc), "warnings": sorted(set(warnings))}


# ------------------------------------------------------------------ audit


class AuditRequest(BaseModel):
    bill: Document
    estimate: Document | None = None
    vehicle: Vehicle
    approvals: list[ApprovalNote] = []
    lang: str = "en"
    old_part_image_b64: str | None = None
    old_part_mime: str | None = None
    old_part_name: str | None = None


@app.post("/api/audit", response_model=AuditReport)
def audit(req: AuditRequest):
    old = None
    if req.old_part_image_b64:
        if req.old_part_mime not in ALLOWED_MIME:
            raise HTTPException(415, "Old-part photo must be JPEG, PNG or WebP.")
        try:
            data = base64.b64decode(req.old_part_image_b64, validate=True)
        except binascii.Error:
            raise HTTPException(400, "Old-part photo is not valid base64.")
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Old-part photo must be under 10 MB.")
        old = (data, req.old_part_mime, req.old_part_name or "part")
    return run_audit(req.bill, req.vehicle, estimate=req.estimate, approvals=req.approvals,
                     lang=req.lang if req.lang in ("en", "hi") else "en", old_part=old,
                     use_models=settings.models_configured, use_search=settings.search_configured)


class MessageRequest(BaseModel):
    report: AuditReport
    lang: str = "en"


@app.post("/api/message")
def redraft(req: MessageRequest):
    lang = req.lang if req.lang in ("en", "hi") else "en"
    if settings.models_configured:
        text, src = message.draft_message(req.report, lang)
    else:
        text, src = message.template_message(req.report, lang), "template"
    return {"message": text, "source": src, "lang": lang}


# ------------------------------------------------------------------ demo


@app.get("/api/demo")
def demo_list():
    out = []
    for p in sorted((ROOT / "eval" / "cases").glob("*.json")):
        case = json.loads(p.read_text(encoding="utf-8"))
        if case.get("demo"):
            out.append({"name": p.stem, "title": case.get("title", p.stem), "description": case.get("description", "")})
    return out


@app.get("/api/demo/{name}")
def demo(name: str, lang: str = "en"):
    path = ROOT / "eval" / "cases" / f"{name}.json"
    if not path.exists() or not name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(404, "unknown demo")
    case = json.loads(path.read_text(encoding="utf-8"))
    if not case.get("demo"):
        raise HTTPException(404, "unknown demo")
    inp = case["input"]
    bill = Document.model_validate(inp["bill"])
    est = Document.model_validate(inp["estimate"]) if inp.get("estimate") else None
    veh = Vehicle.model_validate(inp["vehicle"])
    appr = [ApprovalNote.model_validate(a) for a in inp.get("approvals", [])]
    report = run_audit(bill, veh, estimate=est, approvals=appr, lang=lang if lang in ("en", "hi") else "en",
                       use_models=False, use_search=False)
    return {"title": case.get("title"), "description": case.get("description"), "input": inp,
            "report": report.model_dump(mode="json")}


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
