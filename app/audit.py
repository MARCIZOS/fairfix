"""Run one audit: corrected documents in, AuditReport out.

Order: normalise lines -> fetch public facts (fact pack + prices, in
parallel, each with a timeout) -> deterministic rules -> message.
Nothing here is stored; the report is returned and discarded.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import date

from .config import settings
from .facts import get_pack
from .jobs import UNRECOGNISED, get_job
from .llm import ModelError
from .rules import (
    check_authorization, check_bill_arithmetic, check_coverage, check_line_arithmetic, check_price,
    check_schedule, parse_date, unrecognised_findings,
)
from .schemas import ApprovalNote, AuditReport, Document, Fact, LineResult, OldPartObservation, Vehicle
from .steps import message, normalise, prices


def run_audit(bill: Document, vehicle: Vehicle, *, estimate: Document | None = None,
              approvals: list[ApprovalNote] | None = None, lang: str = "en",
              old_part: tuple[bytes, str, str] | None = None, use_search: bool = True,
              use_models: bool = True, comparables_override: dict[int, list[Fact]] | None = None) -> AuditReport:
    approvals = approvals or []
    warnings: list[str] = []
    bill = bill.model_copy(deep=True)
    estimate = estimate.model_copy(deep=True) if estimate else None

    # --- normalise (user-picked jobs are kept) ---
    warnings += normalise.normalise(bill, use_model=use_models)
    if estimate:
        warnings += normalise.normalise(estimate, use_model=use_models)

    pack = get_pack(vehicle.vehicle_id)
    supported = bool(pack and pack.supported)
    bill_date = parse_date(bill.doc_date.value) or None
    if vehicle.odometer_km is None and bill.odometer_km.value is not None:
        vehicle = vehicle.model_copy(update={"odometer_km": float(bill.odometer_km.value)})

    # --- price comparables, in parallel, never blocking the audit ---
    comps: dict[int, list[Fact]] = dict(comparables_override or {})
    if supported and use_search and comparables_override is None:
        todo = [i for i, ln in enumerate(bill.lines)
                if ln.job and ln.job != UNRECOGNISED and ln.kind in ("part", "consumable")]
        if todo:
            pool = ThreadPoolExecutor(max_workers=min(4, len(todo)))
            futs = {i: pool.submit(prices.lookup_comparables, bill.lines[i], vehicle) for i in todo}
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

    # --- rules ---
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
        try:
            from .steps.old_part import observe
            report.old_part = observe(data, mime, part_name)
        except ModelError as exc:
            report.warnings.append(f"Old-part photo couldn't be read ({exc.kind}).")
            report.old_part = OldPartObservation(result="cannot_determine", observed="The photo could not be analysed.")

    # --- message ---
    if use_models:
        report.message_to_advisor, report.message_source = message.draft_message(report, lang)
    else:
        report.message_to_advisor, report.message_source = message.template_message(report, lang), "template"
    report.message_language = lang
    return report
