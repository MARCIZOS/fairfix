"""Run one audit: corrected documents in, AuditReport out.

Order: normalise lines -> fetch public facts (fact pack + prices, in
parallel, each with a timeout) -> deterministic rules -> message.
Nothing here is stored; the report is returned and discarded.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

from .config import settings
from .facts import get_pack
from .jobs import UNRECOGNISED, get_job
from .llm import ModelError
from .rules import (
    check_authorization, check_bill_arithmetic, check_coverage, check_line_arithmetic, check_price,
    check_schedule, parse_date, unrecognised_findings,
)
from .schemas import ApprovalNote, AuditReport, Document, Fact, LineResult, OldPartObservation, TraceStep, Vehicle
from .steps import message, normalise, prices


def run_audit(bill: Document, vehicle: Vehicle, *, estimate: Document | None = None,
              approvals: list[ApprovalNote] | None = None, lang: str = "en",
              old_part: tuple[bytes, str, str] | None = None, use_search: bool = True,
              use_models: bool = True, comparables_override: dict[int, list[Fact]] | None = None) -> AuditReport:
    approvals = approvals or []
    warnings: list[str] = []
    trace: list[TraceStep] = []
    clock = time.perf_counter
    bill = bill.model_copy(deep=True)
    estimate = estimate.model_copy(deep=True) if estimate else None

    # --- normalise (user-picked jobs are kept) ---
    t0 = clock()
    warnings += normalise.normalise(bill, use_model=use_models)
    if estimate:
        warnings += normalise.normalise(estimate, use_model=use_models)
    src = [ln.job_source for ln in bill.lines]
    by_model = sum(1 for ln in bill.lines if ln.job_source == "model" and ln.job not in (None, UNRECOGNISED))
    parts = [f"{src.count('alias')} by the matching table"]
    if by_model:
        parts.append(f"{by_model} by Nemotron")
    if src.count("user"):
        parts.append(f"{src.count('user')} picked by you")
    unrec = sum(1 for ln in bill.lines if ln.job == UNRECOGNISED)
    if unrec:
        parts.append(f"{unrec} not recognised")
    trace.append(TraceStep(step="map", label="Matched each line to a standard job",
                           engine=display_model(settings.model_text) if by_model or any(s_ == "model" for s_ in src) else "Matching table",
                           ms=(clock() - t0) * 1000, detail=", ".join(parts)))

    t0 = clock()
    pack = get_pack(vehicle.vehicle_id)
    supported = bool(pack and pack.supported)
    n_facts = len(pack.facts) if pack else 0
    trace.append(TraceStep(step="facts", label="Loaded official schedule, warranty and recall facts",
                           engine="Fact pack", ms=(clock() - t0) * 1000,
                           detail=(f"{n_facts} hand-checked facts for {pack.display_name}" if supported else
                                   "Vehicle not supported yet: partial audit"),
                           skipped=not supported))
    bill_date = parse_date(bill.doc_date.value) or None
    if vehicle.odometer_km is None and bill.odometer_km.value is not None:
        vehicle = vehicle.model_copy(update={"odometer_km": float(bill.odometer_km.value)})

    # --- price comparables, in parallel, never blocking the audit ---
    comps: dict[int, list[Fact]] = dict(comparables_override or {})
    t0 = clock()
    searched = 0
    if supported and use_search and comparables_override is None:
        todo = [i for i, ln in enumerate(bill.lines)
                if ln.job and ln.job != UNRECOGNISED and ln.kind in ("part", "consumable")]
        if todo:
            pool = ThreadPoolExecutor(max_workers=min(4, len(todo)))
            futs = {i: pool.submit(prices.lookup_comparables, bill.lines[i], vehicle) for i in todo}
            searched = len(todo)
            budget = settings.search_timeout_s * 3 + settings.model_timeout_s
            for i, fut in futs.items():
                try:
                    facts, w = fut.result(timeout=budget)
                    comps[i] = facts
                    warnings += w
                except FutTimeout:
                    warnings.append("Price lookup timed out for a line; its price check shows Unable to verify.")
                except Exception as exc:  # never let price search break the audit
                    warnings.append(f"Price lookup failed ({type(exc).__name__}).")
            pool.shutdown(wait=False, cancel_futures=True)

    found = sum(len(v) for v in comps.values())
    trace.append(TraceStep(step="prices", label="Searched part prices from credible sellers",
                           engine=f"Tavily + {display_model(settings.model_text)}", ms=(clock() - t0) * 1000,
                           detail=(f"{found} listings for {searched} parts" if searched else
                                   "Price search off" if not use_search else "No parts to price"),
                           skipped=not searched))

    # --- rules ---
    t0 = clock()
    results: list[LineResult] = []
    for i, ln in enumerate(bill.lines):
        job = get_job(ln.job or "")
        if job is None:
            ln.job = UNRECOGNISED
            findings = unrecognised_findings()
        else:
            findings = [
                check_schedule(ln, vehicle, pack, bill_date),
                check_price(ln, comps.get(i, []), vehicle, pack, bill_date, doc=bill),
                check_coverage(ln, vehicle, pack, bill_date),
                check_authorization(ln, estimate, approvals),
            ]
        findings.append(check_line_arithmetic(ln))
        results.append(LineResult(index=i, item=ln, job_label=job.label if job else "Unrecognised item",
                                  findings=findings))

    bill_findings = check_bill_arithmetic(bill)
    n_findings = sum(len(r.findings) for r in results) + len(bill_findings)
    trace.append(TraceStep(step="decide", label="Applied the rules to every line", engine="Python rules",
                           ms=(clock() - t0) * 1000, detail=f"{n_findings} results, each with its evidence"))

    total_q = 0.0
    for r in results:
        if any(f.category == "needs_confirmation" for f in r.findings):
            total_q += float(r.item.line_total_inr.value or 0)

    report = AuditReport(
        lines=results, bill_findings=bill_findings, needs_confirmation_total_inr=round(total_q, 2),
        partial_audit=not supported,
        partial_reason=None if supported else (
            "This vehicle isn't supported yet, so only the arithmetic and authorization checks ran. "
            "Schedule, price and warranty/recall checks show Unable to verify."),
        warnings=sorted(set(warnings)),
    )
    if pack and pack.fictional:
        report.warnings.insert(0, "Sample vehicle: every reference fact is fictional and only shows how the audit works.")

    # --- optional old-part photo: supporting evidence only ---
    if old_part and use_models:
        data, mime, part_name = old_part
        t0 = clock()
        try:
            from .steps.old_part import observe
            report.old_part = observe(data, mime, part_name)
        except ModelError as exc:
            report.warnings.append(f"Old-part photo couldn't be read ({exc.kind}).")
            report.old_part = OldPartObservation(result="cannot_determine", observed="The photo could not be analysed.")
        trace.append(TraceStep(step="photo", label="Looked at the removed part", engine=display_model(settings.model_vision),
                               ms=(clock() - t0) * 1000, detail="Supporting evidence only"))

    # --- message ---
    t0 = clock()
    if use_models:
        report.message_to_advisor, report.message_source = message.draft_message(report, lang)
    else:
        report.message_to_advisor, report.message_source = message.template_message(report, lang), "template"
    report.message_language = lang
    trace.append(TraceStep(step="message", label="Wrote the message to the advisor",
                           engine=display_model(settings.explain_model) if report.message_source == "model" else "Template",
                           ms=(clock() - t0) * 1000,
                           detail="Checked for neutral wording" if report.message_source == "model" else "Deterministic wording"))
    report.trace = trace
    return report


def display_model(model_id: str) -> str:
    """'nvidia/nemotron-3-super-120b-a12b' -> 'Nemotron 3 Super'."""
    m = model_id.lower()
    for key, name in (("3.5-super-vl", "Nemotron 3.5 Super VL"), ("ultra", "Nemotron 3 Ultra"), ("super", "Nemotron 3 Super"),
                      ("nano", "Nemotron 3 Nano"), ("lightning", "Nemotron 3.5 Lightning")):
        if key in m:
            return name
    return model_id.split("/")[-1]
