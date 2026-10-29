"""Deterministic rules. Every verdict FairFix shows is produced here.

No model is called from this module. Each check returns a `Finding` with one
of four categories, the rule applied, the exact inputs, the evidence, the
remaining uncertainty and a recommended next step.

Wording policy: labels are fixed strings written to ask for clarification.
They never say fraud, overcharged, unauthorised or unnecessary
(see app/guard.py, which tests every label).
"""

from __future__ import annotations

from datetime import date
from typing import Iterable
from urllib.parse import urlparse

from .config import CREDIBLE_SELLER_DOMAINS, Settings, settings as default_settings
from .facts import FactPack
from .jobs import UNRECOGNISED, get_job
from .schemas import ApprovalNote, Document, Fact, Finding, LineItem, Vehicle

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def months_between(start: date, end: date) -> float:
    """Elapsed months as a float (whole months plus the day fraction)."""
    whole = (end.year - start.year) * 12 + (end.month - start.month)
    day_frac = (end.day - start.day) / 30.4375
    return round(whole + day_frac, 2)


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def parse_month(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    try:
        y, m = str(s)[:7].split("-")
        return int(y), int(m)
    except ValueError:
        return None


def inr(x: float | None) -> str:
    if x is None:
        return "?"
    return f"₹{x:,.0f}" if abs(x - round(x)) < 0.005 else f"₹{x:,.2f}"


def norm_text(s: str | None) -> str:
    return " ".join((s or "").lower().replace("-", " ").replace("/", " ").split())


def norm_part_no(s: str | None) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def is_credible_seller(url: str) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in CREDIBLE_SELLER_DOMAINS)


def unit_price(line: LineItem) -> float | None:
    if line.unit_price_inr.value is not None:
        return float(line.unit_price_inr.value)
    q, t = line.qty.value, line.line_total_inr.value
    if q and t is not None and q > 0:
        return float(t) / float(q)
    if t is not None and q is None:
        return float(t)  # single unit assumed only when no quantity is printed
    return None


def _f(check, category, label, rule, *, inputs=None, evidence=None, uncertainty=None,
       next_step="", verify_url=None, calculation=None) -> Finding:
    return Finding(check=check, category=category, label=label, rule=rule,
                   inputs=inputs or {}, evidence=list(evidence or []),
                   uncertainty=uncertainty, next_step=next_step,
                   verify_url=verify_url, calculation=calculation)


UNSUPPORTED_NOTE = "Vehicle not yet supported: no hand-checked fact pack, so this check can't be verified."


def unrecognised_findings() -> list[Finding]:
    msg = "Item not recognised: ask the advisor what this item is"
    rule = "line_job == 'unrecognised' -> every reference check is unable_to_verify"
    return [
        _f(check, "unable_to_verify", msg, rule,
           uncertainty="FairFix couldn't confidently map this bill text to a standard job, so it doesn't guess.",
           next_step="Ask the advisor what this line is for; you can then pick the job on the correction screen.")
        for check in ("schedule", "price", "coverage", "authorization")
    ]


# --------------------------------------------------------------------------
# schedule
# --------------------------------------------------------------------------


def _baseline(vehicle: Vehicle, job: str) -> tuple[str, float | None, date | None]:
    """Where to measure the interval from: (mode, km, date)."""
    for h in vehicle.history:
        if h.job == job:
            if h.status == "replaced":
                return "replaced", h.km, h.on
            if h.status == "never":
                return "never", 0.0, vehicle.purchase_date or vehicle.registration_date
    return "unknown", None, None


def _schedule_absence_evidence(pack: FactPack, vehicle: Vehicle, job_label: str) -> list[Fact]:
    """Evidence that an item is NOT in the official schedule: the schedule itself.

    Returns [] unless the pack holds official, human-verified schedule facts;
    absence can only be shown against a complete official schedule.
    """
    official, _sec, _r = pack.facts_for("schedule", vehicle)
    if not official:
        return []
    def label(jid: str) -> str:
        j = get_job(jid)
        return j.label if j else jid

    labels = sorted({label(f.data.get("job", "?")) for f in official})
    dates = [f.checked_on for f in official if f.checked_on]
    return [Fact(kind="schedule", source_url=official[0].source_url, excerpt="",
                 checked_on=max(dates) if dates else None, source_rank=1, verified_by_human=True, sufficient=True,
                 data={"note": f"The official schedule in the fact pack lists: {', '.join(labels)}. "
                               f"{job_label} is not among them."})]


def check_schedule(line: LineItem, vehicle: Vehicle, pack: FactPack | None, bill_date: date | None,
                   s: Settings = default_settings) -> Finding:
    job_id = line.job
    job = get_job(job_id or "")
    if job is None:
        return unrecognised_findings()[0]
    if pack is None or not pack.supported:
        return _f("schedule", "unable_to_verify", "Schedule not verified", "vehicle_supported == False",
                  uncertainty=UNSUPPORTED_NOTE, next_step="Check the owner's manual for this item's interval.")

    if job.kind == "misc":
        return _f("schedule", "not_applicable", "Not applicable: consumable not covered by the service schedule",
                  "job.kind == 'misc'")
    if job.kind == "labour":
        return _f("schedule", "not_applicable", "Not applicable: labour charge", "job.kind == 'labour'")
    if job.kind == "wear_repair":
        return _f("schedule", "not_applicable",
                  "Not applicable: replaced on condition after inspection, not on a fixed schedule",
                  "job.kind == 'wear_repair'",
                  next_step="If you weren't shown the worn part or told the inspection finding, you can ask for it.")

    sufficient, secondary, reasons = pack.facts_for("schedule", vehicle, job.id)
    absence = _schedule_absence_evidence(pack, vehicle, job.label)

    if not sufficient:
        if job.kind == "add_on" and pack.schedule_complete and absence:
            return _f("schedule", "needs_confirmation",
                      "Not listed in the official service schedule: ask why it's recommended",
                      "job.kind == 'add_on' and job not in complete official schedule",
                      inputs={"job": job.id, "schedule_complete": True},
                      evidence=absence,
                      uncertainty="It may still be optional or recommended for a specific reason; the schedule just doesn't list it.",
                      next_step="Ask what problem this treatment addresses and whether it's optional.")
        if job.kind == "maintenance" and pack.schedule_complete and absence and not secondary and not reasons:
            return _f("schedule", "needs_confirmation",
                      "Not listed in the official service schedule: ask for the inspection finding",
                      "job not in complete official schedule",
                      inputs={"job": job.id, "schedule_complete": True},
                      evidence=absence,
                      next_step="Ask what inspection finding led to this replacement.")
        if secondary:
            return _f("schedule", "unable_to_verify",
                      "Reported schedule only: confirm with the owner's manual",
                      "only secondary sources found -> cannot decide",
                      evidence=secondary,
                      uncertainty="Only a secondary source was found; it can point you to the manual but can't decide this.",
                      next_step="Check the interval in the owner's manual or ask the advisor to show it.")
        return _f("schedule", "unable_to_verify", "Schedule not verified",
                  "no sufficient schedule fact for this job",
                  uncertainty="; ".join(sorted(set(reasons))) or "No official interval for this item is in the fact pack.",
                  next_step="Check the interval in the owner's manual.")

    # conflicting official facts -> disclose, don't pick
    keys = {(f.data.get("interval_km"), f.data.get("interval_months"), f.data.get("condition", "whichever_first"),
             f.data.get("action", "replace")) for f in sufficient}
    if len(keys) > 1:
        return _f("schedule", "unable_to_verify", "Schedule not verified: official sources disagree",
                  "len(distinct sufficient intervals) > 1", evidence=sufficient,
                  uncertainty="Two official sources give different intervals for this vehicle; FairFix won't pick one.",
                  next_step="Ask the advisor which schedule applies to your variant and model year.")

    fact = sufficient[0]
    ikm = fact.data.get("interval_km")
    imo = fact.data.get("interval_months")
    cond = fact.data.get("condition", "whichever_first")
    action = fact.data.get("action", "replace")
    window = s.schedule_window_pct / 100.0
    odo = vehicle.odometer_km
    mode, base_km, base_on = _baseline(vehicle, job.id)

    inputs = {"job": job.id, "interval_km": ikm, "interval_months": imo, "condition": cond,
              "action": action, "odometer_km": odo, "bill_date": str(bill_date) if bill_date else None,
              "history": mode, "last_km": base_km, "last_date": str(base_on) if base_on else None,
              "early_window_pct": s.schedule_window_pct}

    if action == "inspect":
        return _f("schedule", "needs_confirmation",
                  "Scheduled for inspection, not routine replacement: ask for the inspection finding",
                  "official schedule action == 'inspect'", inputs=inputs, evidence=[fact],
                  uncertainty="Replacement is allowed when inspection finds wear; FairFix can't see the inspection.",
                  next_step="Ask what the inspection found.")

    km_state: str | None = None   # "due" | "not_due" | None (unknown)
    mo_state: str | None = None
    calc: list[str] = []

    if mode in ("replaced", "never"):
        if ikm and odo is not None and base_km is not None:
            km_elapsed = odo - base_km
            km_state = "due" if km_elapsed >= ikm * (1 - window) else "not_due"
            calc.append(f"km since last replacement: {odo:,.0f} − {base_km:,.0f} = {km_elapsed:,.0f} (interval {ikm:,} km)")
        if imo and base_on and bill_date:
            mo_elapsed = months_between(base_on, bill_date)
            mo_state = "due" if mo_elapsed >= imo * (1 - window) else "not_due"
            calc.append(f"months since last replacement: {mo_elapsed:.1f} (interval {imo} months)")
    else:
        # History unknown: only a match to a scheduled milestone from new can support the line.
        start = vehicle.purchase_date or vehicle.registration_date
        if ikm and odo is not None:
            k = round(odo / ikm)
            near = k >= 1 and abs(odo - k * ikm) <= ikm * window
            km_state = "due" if near else None
            calc.append(f"odometer {odo:,.0f} km vs milestones every {ikm:,} km: {'at' if near else 'not at'} a milestone")
        if imo and start and bill_date:
            m = months_between(start, bill_date)
            k = round(m / imo)
            near = k >= 1 and abs(m - k * imo) <= imo * window
            mo_state = "due" if near else None
            calc.append(f"age {m:.1f} months vs milestones every {imo} months: {'at' if near else 'not at'} a milestone")

    def due_label(by: list[str]) -> str:
        return "Matches schedule (due by " + (" and ".join(by)) + ")"

    by = [n for n, st in (("km", km_state), ("time", mo_state)) if st == "due"]

    if cond in ("whichever_first", "km_only", "months_only"):
        if cond == "km_only":
            mo_state = "n/a"
            by = ["km"] if km_state == "due" else []
        if cond == "months_only":
            km_state = "n/a"
            by = ["time"] if mo_state == "due" else []
        if by:
            unc = None
            if mode == "unknown":
                unc = "When this item was last replaced is unknown; this assumes regular servicing at the scheduled milestones."
            return _f("schedule", "supported", due_label(by), f"{cond}: due if any limit reached (early window {s.schedule_window_pct:.0f}%)",
                      inputs=inputs, evidence=[fact], uncertainty=unc, calculation="; ".join(calc))
        if mode == "unknown":
            return _f("schedule", "unable_to_verify",
                      "Schedule not verified: when this item was last replaced is unknown",
                      "history unknown and not at a scheduled milestone",
                      inputs=inputs, evidence=[fact], calculation="; ".join(calc),
                      uncertainty="It may be due if it wasn't replaced at the last milestone.",
                      next_step="Tell FairFix when this item was last replaced (or that it never was) on the correction screen.")
        known = [st for st in (km_state, mo_state) if st not in (None, "n/a")]
        unknown_dims = [st for st in (km_state, mo_state) if st is None]
        if unknown_dims or not known:
            return _f("schedule", "unable_to_verify", "Schedule not verified: missing odometer or dates",
                      "a limit could not be evaluated, so 'not due' can't be shown",
                      inputs=inputs, evidence=[fact], calculation="; ".join(calc),
                      next_step="Add the odometer reading, bill date and purchase date.")
        return _f("schedule", "needs_confirmation", "Not scheduled at this interval: ask for the inspection finding",
                  f"{cond}: neither limit reached", inputs=inputs, evidence=[fact], calculation="; ".join(calc),
                  uncertainty="Damage or unusual wear can justify replacing earlier than the schedule.",
                  next_step="Ask what inspection finding led to replacing it now.")

    # whichever_later: both limits must be reached
    if km_state == "due" and mo_state == "due":
        return _f("schedule", "supported", due_label(["km", "time"]), "whichever_later: due when both limits reached",
                  inputs=inputs, evidence=[fact], calculation="; ".join(calc))
    if "not_due" in (km_state, mo_state):
        return _f("schedule", "needs_confirmation", "Not scheduled at this interval: ask for the inspection finding",
                  "whichever_later: a limit not reached", inputs=inputs, evidence=[fact], calculation="; ".join(calc),
                  uncertainty="Damage or unusual wear can justify replacing earlier than the schedule.",
                  next_step="Ask what inspection finding led to replacing it now.")
    return _f("schedule", "unable_to_verify", "Schedule not verified: missing odometer, dates or history",
              "whichever_later: a limit could not be evaluated", inputs=inputs, evidence=[fact],
              calculation="; ".join(calc), next_step="Add the odometer reading, dates and when this item was last replaced.")


# --------------------------------------------------------------------------
# price
# --------------------------------------------------------------------------


def _to_basis(price: float, included: bool | None, rate_pct: float | None, want_included: bool) -> float | None:
    if included is None:
        return None
    if included == want_included:
        return price
    if rate_pct is None:
        return None
    r = rate_pct / 100.0
    return price * (1 + r) if want_included else price / (1 + r)


def check_price(line: LineItem, comparables: Iterable[Fact], vehicle: Vehicle, pack: FactPack | None,
                bill_date: date | None, s: Settings = default_settings, doc: Document | None = None) -> Finding:
    job = get_job(line.job or "")
    if job is None:
        return unrecognised_findings()[1]
    if pack is None or not pack.supported:
        return _f("price", "unable_to_verify", "Unable to verify price fairly", "vehicle_supported == False",
                  uncertainty=UNSUPPORTED_NOTE)

    thr = s.price_review_threshold_pct
    up = unit_price(line)
    inputs = {"job": job.id, "line_kind": line.kind, "grade": line.grade,
              "part_number": line.part_number.value, "unit_price_inr": up,
              "threshold_pct": thr, "min_comparables": s.price_min_comparables, "min_sellers": s.price_min_sellers}

    if line.kind in ("labour",) or job.kind == "labour":
        labour, _sec, _r = pack.facts_for("labour_price", vehicle, job.id)
        if not labour or up is None:
            return _f("price", "unable_to_verify", "Unable to verify price fairly",
                      "no official labour rate for this job",
                      inputs=inputs, uncertainty="No published labour rate for this job is in the fact pack.",
                      next_step="Ask the advisor for the standard labour time and rate for this job.")
        top = max(float(f.data.get("price_inr", 0)) for f in labour)
        limit = top * (1 + thr / 100)
        calc = f"line {inr(up)} vs published rate {inr(top)}; review limit = {inr(top)} × {1 + thr / 100:.2f} = {inr(limit)}"
        if up <= limit + 0.005:
            return _f("price", "supported", "Within the published labour rate", "labour <= published × (1+threshold)",
                      inputs=inputs, evidence=labour, calculation=calc)
        return _f("price", "needs_confirmation",
                  f"Labour needs review against the published rate: {inr(up - top)} above it",
                  "labour > published × (1+threshold)", inputs=inputs, evidence=labour, calculation=calc,
                  next_step="Ask how the labour charge was calculated.")

    if line.kind in ("part_with_fitting", "unknown"):
        return _f("price", "unable_to_verify", "Unable to verify price fairly",
                  "line mixes part and labour, or its type is unclear",
                  inputs=inputs,
                  uncertainty="This line can't be compared with part-only listings without separating the fitting charge.",
                  next_step="Ask for the part and labour amounts to be shown separately.")
    if up is None:
        return _f("price", "unable_to_verify", "Unable to verify price fairly", "no unit price on the bill",
                  inputs=inputs, next_step="Correct the amount on the correction screen if it was misread.")
    line_incl = line_gst_included(line, doc)
    inputs["line_price_includes_gst"] = line_incl
    if line_incl is None:
        return _f("price", "unable_to_verify", "Unable to verify price fairly",
                  "bill doesn't show whether this price includes GST", inputs=inputs,
                  uncertainty="Comparing a tax-inclusive price with a tax-exclusive one would be misleading.",
                  next_step="Mark on the correction screen whether the bill's prices include GST.")
    pn = norm_part_no(line.part_number.value)
    if not pn and line.grade == "unknown":
        return _f("price", "unable_to_verify", "Unable to verify price fairly",
                  "no part number and unknown grade -> no equivalent comparables possible", inputs=inputs,
                  uncertainty="Without a part number or the part's grade (genuine, OEM, aftermarket), listings can't be matched like for like.",
                  next_step="Ask for the part number, or mark the grade on the correction screen.")

    used: list[tuple[Fact, float]] = []
    excluded: dict[str, int] = {}
    seen_urls: set[str] = set()

    def skip(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for c in comparables:
        d = c.data
        if c.kind != "part_price" or not c.source_url or not c.excerpt.strip():
            skip("no source or excerpt"); continue
        if c.source_url in seen_urls:
            skip("duplicate listing"); continue
        if pn:
            if norm_part_no(d.get("part_number")) != pn:
                skip("different part number"); continue
        else:
            if d.get("job") != job.id or d.get("grade") != line.grade:
                skip("different part or grade"); continue
        if d.get("listing_type", "part_only") != "part_only":
            skip("installed price, not part-only"); continue
        if not is_credible_seller(c.source_url):
            skip("seller not on credible list"); continue
        if c.checked_on is None or ((bill_date or date.today()) - c.checked_on).days > s.price_max_age_days:
            skip(f"older than {s.price_max_age_days} days or undated"); continue
        if d.get("in_stock") is False or d.get("delivers_to_city") is False:
            skip("out of stock or no delivery to your city"); continue
        price = d.get("price_inr")
        if price is None:
            skip("no price"); continue
        p = _to_basis(float(price), d.get("gst_included"), d.get("gst_rate_pct"), line_incl)
        if p is None:
            skip("tax basis unknown"); continue
        seen_urls.add(c.source_url)
        used.append((c, p))

    sellers = {domain_of(c.source_url) for c, _ in used}
    inputs.update({"comparables_used": len(used), "distinct_sellers": len(sellers), "excluded": excluded})

    if len(used) < s.price_min_comparables or len(sellers) < s.price_min_sellers:
        return _f("price", "unable_to_verify", "Unable to verify price fairly",
                  f"need >= {s.price_min_comparables} equivalent comparables from >= {s.price_min_sellers} sellers",
                  inputs=inputs, evidence=[c for c, _ in used],
                  uncertainty=f"Found {len(used)} equivalent listing(s) from {len(sellers)} seller(s).",
                  next_step="Ask for the part number and MRP printed on the part's packaging.")

    prices = sorted(p for _, p in used)
    top = prices[-1]
    limit = top * (1 + thr / 100)
    basis = "incl. GST" if line_incl else "excl. GST"
    calc = (f"comparables ({basis}): " + ", ".join(inr(p) for p in prices) +
            f"; review limit = highest {inr(top)} × {1 + thr / 100:.2f} = {inr(limit)}; bill unit price {inr(up)}")
    evidence = [c.model_copy(update={"sufficient": True}) for c, _ in used]
    if up <= limit + 0.005:   # half a paisa: absorbs float error at exactly the threshold
        return _f("price", "supported", "Within the comparable range" if up <= top else "Within the review threshold of comparable listings",
                  f"unit_price <= highest_comparable × (1 + {thr:g}%)", inputs=inputs, evidence=evidence, calculation=calc)
    return _f("price", "needs_confirmation",
              f"Price needs review against available listings: {inr(up - top)} above the highest of {len(prices)} comparables",
              f"unit_price > highest_comparable × (1 + {thr:g}%)", inputs=inputs, evidence=evidence, calculation=calc,
              uncertainty="Listings can differ in stock, delivery and seller; this is a reason to ask, not a finding of overpricing.",
              next_step="Ask how this part was priced and for its MRP.")


def line_gst_included(line: LineItem, doc: Document | None = None) -> bool | None:
    """Tax basis of a line: the line's own flag, else the document's setting."""
    if line.gst_included.value is not None:
        return bool(line.gst_included.value)
    if doc is not None and doc.prices_include_tax.value is not None:
        return bool(doc.prices_include_tax.value)
    return None


# --------------------------------------------------------------------------
# coverage: warranty and recall
# --------------------------------------------------------------------------


def _recall_result(job_id: str, vehicle: Vehicle, pack: FactPack) -> tuple[Finding | None, str]:
    sufficient, secondary, _ = pack.facts_for("recall", vehicle)
    note_parts: list[str] = []
    mfg = parse_month(vehicle.mfg_month)
    for f in sufficient:
        d = f.data
        if job_id not in (d.get("affected_jobs") or []):
            continue
        lo, hi = parse_month(d.get("mfg_from")), parse_month(d.get("mfg_to"))
        if mfg is None:
            note_parts.append("A recall involving this part exists for the model, but your manufacturing month is needed to see if it could apply.")
            continue
        if lo and mfg < lo or hi and mfg > hi:
            continue
        return _f("coverage", "needs_confirmation",
                  "Potential recall match: verify eligibility using your chassis number",
                  "job in recall.affected_jobs and mfg_month within recall period",
                  inputs={"job": job_id, "mfg_month": vehicle.mfg_month, "recall_period": [d.get("mfg_from"), d.get("mfg_to")],
                          "variant": vehicle.variant},
                  evidence=[f],
                  uncertainty="Only the manufacturer can confirm eligibility, usually by VIN or chassis number.",
                  next_step="Check eligibility on the manufacturer's recall lookup before paying for this line.",
                  verify_url=pack.recall_lookup_url or None), ""
    for f in secondary:
        if job_id in (f.data.get("affected_jobs") or []):
            note_parts.append("A news report mentions a recall involving this part; check with the manufacturer.")
    if pack.recall_sources_checked:
        names = ", ".join(src.get("name") or domain_of(src.get("url", "")) for src in pack.recall_sources_checked)
        note_parts.append(f"No matching recall found in the sources checked ({names}).")
    else:
        note_parts.append("Recalls were not checked for this vehicle.")
    return None, " ".join(note_parts)


def check_coverage(line: LineItem, vehicle: Vehicle, pack: FactPack | None, bill_date: date | None,
                   s: Settings = default_settings) -> Finding:
    job = get_job(line.job or "")
    if job is None:
        return unrecognised_findings()[2]
    if pack is None or not pack.supported:
        return _f("coverage", "unable_to_verify", "Warranty eligibility could not be verified", "vehicle_supported == False",
                  uncertainty=UNSUPPORTED_NOTE)

    recall, recall_note = _recall_result(job.id, vehicle, pack)
    if recall:
        return recall

    sufficient, secondary, reasons = pack.facts_for("warranty", vehicle)
    if not sufficient:
        return _f("coverage", "unable_to_verify", "Warranty eligibility could not be verified",
                  "no sufficient warranty fact", evidence=secondary,
                  uncertainty=" ".join(filter(None, ["; ".join(sorted(set(reasons))), recall_note])) or None,
                  next_step="Check the warranty booklet or the manufacturer's site.")
    w = sufficient[0]
    d = w.data
    inputs = {"job": job.id, "job_kind": job.kind, "warranty_years": d.get("years"), "warranty_km": d.get("km"),
              "starts_from": d.get("starts_from", "purchase"), "odometer_km": vehicle.odometer_km,
              "purchase_date": str(vehicle.purchase_date) if vehicle.purchase_date else None,
              "registration_date": str(vehicle.registration_date) if vehicle.registration_date else None,
              "bill_date": str(bill_date) if bill_date else None}

    if job.kind in (d.get("excluded_job_kinds") or []) or job.id in (d.get("excluded_jobs") or []):
        return _f("coverage", "not_applicable", "Not applicable: excluded under the warranty terms",
                  "job or job kind in warranty exclusions", inputs=inputs, evidence=[w], uncertainty=recall_note or None)

    start = vehicle.purchase_date if d.get("starts_from", "purchase") == "purchase" else vehicle.registration_date
    if start is None or vehicle.odometer_km is None or bill_date is None or d.get("years") is None or d.get("km") is None:
        return _f("coverage", "unable_to_verify", "Warranty eligibility could not be verified",
                  "missing start date, odometer, bill date or warranty limits", inputs=inputs, evidence=[w],
                  uncertainty=recall_note or None,
                  next_step="Add the purchase or registration date and odometer reading.")
    months = months_between(start, bill_date)
    within_time = months < float(d["years"]) * 12
    within_km = vehicle.odometer_km < float(d["km"])
    calc = f"{months:.1f} months of {float(d['years']) * 12:.0f}; {vehicle.odometer_km:,.0f} km of {float(d['km']):,.0f}"
    if within_time and within_km:
        return _f("coverage", "needs_confirmation", "Potential warranty coverage: verify with the manufacturer",
                  "within warranty years and km, job not excluded", inputs=inputs, evidence=[w], calculation=calc,
                  uncertainty=" ".join(filter(None, [
                      "Warranty covers defects, not damage or wear; only the manufacturer or dealer can confirm a claim.",
                      recall_note])),
                  next_step="Ask whether this repair can be done under warranty before paying.")
    which = "time" if not within_time else "km"
    return _f("coverage", "supported", f"Outside the warranty period (by {which})",
              "warranty limit exceeded", inputs=inputs, evidence=[w], calculation=calc,
              uncertainty=recall_note or None)


# --------------------------------------------------------------------------
# authorization
# --------------------------------------------------------------------------


def _same_item(a: LineItem, b: LineItem) -> bool:
    if a.job and b.job and a.job != UNRECOGNISED and a.job == b.job:
        return True
    return norm_text(a.raw_text.value) == norm_text(b.raw_text.value) != ""


def check_authorization(line: LineItem, estimate: Document | None, approvals: list[ApprovalNote],
                        s: Settings = default_settings) -> Finding:
    if estimate is None or not estimate.lines:
        return _f("authorization", "not_applicable", "Not applicable: no estimate uploaded",
                  "estimate is None")
    if not line.job or line.job == UNRECOGNISED:
        return unrecognised_findings()[3]
    tol = s.estimate_amount_tolerance_pct / 100
    amt = line.line_total_inr.value
    for e in estimate.lines:
        if _same_item(line, e):
            est_amt = e.line_total_inr.value
            inputs = {"bill_amount": amt, "estimate_amount": est_amt, "tolerance_pct": s.estimate_amount_tolerance_pct}
            if amt is not None and est_amt is not None and amt > est_amt * (1 + tol) + 0.5:
                return _f("authorization", "needs_confirmation",
                          f"On the estimate, but {inr(amt - est_amt)} more than estimated: confirm the change was approved",
                          "bill_amount > estimate_amount × (1 + tolerance)", inputs=inputs,
                          calculation=f"{inr(amt)} vs estimate {inr(est_amt)}",
                          next_step="Ask what changed from the estimate.")
            return _f("authorization", "supported", "On the estimate", "item matches an estimate line", inputs=inputs)
    for n in approvals:
        if (n.job and n.job == line.job) or (norm_text(n.item) and norm_text(n.item) in norm_text(line.raw_text.value)):
            inputs = {"approval": n.model_dump(mode="json"), "bill_amount": amt}
            if n.amount_inr is not None and amt is not None and amt > n.amount_inr * (1 + tol) + 0.5:
                return _f("authorization", "needs_confirmation",
                          f"Approved later (recorded by you), but {inr(amt - n.amount_inr)} more than you noted",
                          "bill_amount > recorded approval × (1 + tolerance)", inputs=inputs,
                          next_step="Ask why the amount differs from what you approved.")
            return _f("authorization", "supported", "Approved later (recorded by you)",
                      "item matches a recorded approval note", inputs=inputs,
                      uncertainty="Based on your own note of the approval.")
    return _f("authorization", "needs_confirmation", "Not present in the uploaded estimate: confirm authorization",
              "no matching estimate line or recorded approval",
              inputs={"bill_amount": amt},
              uncertainty="It may have been approved verbally, in a revised estimate or as an urgent repair.",
              next_step="If you approved it later, add a note; otherwise ask when it was approved.")


# --------------------------------------------------------------------------
# arithmetic
# --------------------------------------------------------------------------


def check_line_arithmetic(line: LineItem, s: Settings = default_settings) -> Finding:
    q, u, t = line.qty.value, line.unit_price_inr.value, line.line_total_inr.value
    if t is None:
        return _f("arithmetic", "unable_to_verify", "Line amount not readable", "line_total missing",
                  next_step="Correct the amount on the correction screen.")
    if q is None or u is None:
        return _f("arithmetic", "not_applicable", "Not applicable: only a line amount is printed",
                  "qty or unit price not printed")
    expected = float(q) * float(u)
    diff = float(t) - expected
    calc = f"{q:g} × {inr(u)} = {inr(expected)}; printed {inr(t)}"
    if abs(diff) <= s.line_rounding_tolerance_inr:
        return _f("arithmetic", "supported", "Line arithmetic adds up", f"|qty × unit − total| <= ₹{s.line_rounding_tolerance_inr:g}",
                  inputs={"qty": q, "unit": u, "total": t}, calculation=calc)
    return _f("arithmetic", "needs_confirmation", f"Line total differs from quantity × unit price by {inr(abs(diff))}",
              f"|qty × unit − total| > ₹{s.line_rounding_tolerance_inr:g}", inputs={"qty": q, "unit": u, "total": t},
              calculation=calc, next_step="Check this line wasn't misread, then ask the advisor about it.")


def check_bill_arithmetic(doc: Document, s: Settings = default_settings) -> list[Finding]:
    out: list[Finding] = []
    tol = s.total_rounding_tolerance_inr
    totals = [ln.line_total_inr.value for ln in doc.lines]
    all_totals = all(t is not None for t in totals) and bool(totals)
    line_sum = sum(float(t) for t in totals if t is not None)

    sub = doc.subtotal_inr.value
    incl = doc.prices_include_tax.value
    tax_amounts = [t.amount_inr for t in doc.tax_lines]
    tax_known = bool(tax_amounts) and all(a is not None for a in tax_amounts)
    tax = sum(float(a) for a in tax_amounts if a is not None)
    if sub is None:
        out.append(_f("arithmetic", "not_applicable", "Not applicable: no subtotal printed", "subtotal missing"))
    elif not all_totals:
        out.append(_f("arithmetic", "unable_to_verify", "Subtotal not verified: some line amounts unreadable",
                      "line totals missing"))
    else:
        d = float(sub) - line_sum
        calc = f"sum of lines {inr(line_sum)}; printed subtotal {inr(sub)}"
        if abs(d) <= tol:
            out.append(_f("arithmetic", "supported", "Subtotal matches the lines", f"|Σ lines − subtotal| <= ₹{tol:g}", calculation=calc))
        elif incl is True and tax_known and tax > 0 and abs(float(sub) + tax - line_sum) <= tol:
            # Tax-inclusive lines with a printed taxable value: taxable value + tax = sum of lines.
            out.append(_f("arithmetic", "supported", "Subtotal is the taxable value; with tax it matches the lines",
                          f"prices include tax and |subtotal + tax − Σ lines| <= ₹{tol:g}",
                          calculation=f"taxable {inr(sub)} + tax {inr(tax)} = {inr(float(sub) + tax)}; sum of lines {inr(line_sum)}"))
        else:
            out.append(_f("arithmetic", "needs_confirmation", f"Subtotal differs from the sum of lines by {inr(abs(d))}",
                          f"|Σ lines − subtotal| > ₹{tol:g}", calculation=calc,
                          next_step="Check for a misread line, then ask the advisor."))

    gt = doc.grand_total_inr.value
    if gt is None:
        out.append(_f("arithmetic", "unable_to_verify", "Grand total not readable", "grand_total missing",
                      next_step="Correct the total on the correction screen."))
        return out
    if incl is True and all_totals:
        base = line_sum          # tax-inclusive line amounts already contain the tax
    else:
        base = float(sub) if sub is not None else (line_sum if all_totals else None)
    if base is None:
        out.append(_f("arithmetic", "unable_to_verify", "Grand total not verified: some line amounts unreadable", "base unknown"))
        return out
    disc = float(doc.discount_inr.value or 0)
    when = doc.discount_applied.value
    if doc.tax_lines and not tax_known:
        out.append(_f("arithmetic", "unable_to_verify", "Grand total not verified: a tax amount is unreadable", "tax amount missing"))
        return out
    if disc and incl is not True and tax and when is None:
        out.append(_f("arithmetic", "unable_to_verify",
                      "Grand total not verified: unclear whether the discount was applied before or after tax",
                      "discount present and application unknown",
                      next_step="Mark how the discount was applied on the correction screen."))
        return out
    if incl is None and doc.tax_lines:
        out.append(_f("arithmetic", "unable_to_verify", "Grand total not verified: unclear whether prices include tax",
                      "prices_include_tax unknown with tax lines present",
                      next_step="Mark whether the bill's prices include GST on the correction screen."))
        return out
    ro = float(doc.round_off_inr.value or 0)
    if incl:
        expected = base - disc + ro
        formula = f"{inr(base)} − discount {inr(disc)} + round-off {inr(ro)} (prices include tax)"
    else:
        expected = base - disc + tax + ro
        formula = f"{inr(base)} − discount {inr(disc)} + tax {inr(tax)} + round-off {inr(ro)}"
    d = float(gt) - expected
    calc = f"{formula} = {inr(expected)}; printed total {inr(gt)}"
    if abs(d) <= tol:
        out.append(_f("arithmetic", "supported", "Totals reconcile with the bill's own structure",
                      f"|expected − grand_total| <= ₹{tol:g}", calculation=calc))
    else:
        out.append(_f("arithmetic", "needs_confirmation",
                      f"Grand total differs from the bill's own lines and taxes by {inr(abs(d))}",
                      f"|expected − grand_total| > ₹{tol:g}", calculation=calc,
                      next_step="Check for misread amounts, then ask the advisor how the total was reached."))
    return out
