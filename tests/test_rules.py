"""Unit tests for app/rules.py: every row of the category mapping table,
the schedule edge cases, price comparables, coverage, authorization and
bill-aware arithmetic. Run with:  pytest -q
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app import rules
from app.config import settings
from app.guard import accusatory_terms
from app.schemas import ApprovalNote, Document
from tests.helpers import BILL_DATE, F, doc, hist, line, pack, price_fact, vehicle

P = pack()


def cat(f):
    return f.category


# ---------------------------------------------------------------- schedule


class TestSchedule:
    def test_due_by_time_even_though_km_not_reached(self):
        # The plan's example: 9,800 km, 14 months, "12,000 km or 12 months, whichever first", never replaced
        v = vehicle(history=[hist("air_filter_replacement", "never")])
        f = rules.check_schedule(line("AIR CLNR ELEMENT", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.category == "supported"
        assert f.label == "Matches schedule (due by time)"

    def test_due_by_km_only(self):
        v = vehicle(odometer_km=12500, purchase_date=date(2026, 4, 1), history=[hist("air_filter_replacement", "never")])
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.category == "supported" and f.label == "Matches schedule (due by km)"

    def test_due_by_both(self):
        v = vehicle(odometer_km=13000, history=[hist("air_filter_replacement", "never")])
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.label == "Matches schedule (due by km and time)"

    def test_neither_reached_needs_confirmation(self):
        v = vehicle(odometer_km=9800, history=[hist("air_filter_replacement", "replaced", km=5000, on=date(2026, 6, 1))])
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.category == "needs_confirmation"
        assert f.label == "Not scheduled at this interval: ask for the inspection finding"
        assert f.calculation and "4,800" in f.calculation

    def test_early_window_counts_as_due(self):
        # 10,900 km since replacement is within 10% of 12,000
        v = vehicle(odometer_km=15900, history=[hist("air_filter_replacement", "replaced", km=5000, on=date(2026, 6, 1))])
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.category == "supported"

    def test_missing_odometer_cannot_show_not_due(self):
        v = vehicle(odometer_km=None, history=[hist("air_filter_replacement", "replaced", km=5000, on=date(2026, 6, 1))])
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.category == "unable_to_verify"

    def test_missing_dates_but_km_due_is_still_supported(self):
        v = vehicle(odometer_km=17500, purchase_date=None, history=[hist("air_filter_replacement", "replaced", km=5000, on=None)])
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, None)
        assert f.category == "supported" and "km" in f.label

    def test_unknown_history_at_milestone_supported_with_uncertainty(self):
        v = vehicle(odometer_km=12100)
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.category == "supported" and f.uncertainty

    def test_unknown_history_off_milestone_is_unable_not_accusation(self):
        v = vehicle(odometer_km=9800, purchase_date=date(2025, 12, 20))
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), v, P, BILL_DATE)
        assert f.category == "unable_to_verify"

    def test_months_only_rule(self):
        v = vehicle(history=[hist("brake_fluid_replacement", "never")])
        f = rules.check_schedule(line("DOT 3", "brake_fluid_replacement"), v, P, BILL_DATE)
        assert f.category == "needs_confirmation"  # 14 months < 24
        v2 = vehicle(purchase_date=date(2024, 9, 1), history=[hist("brake_fluid_replacement", "never")])
        assert rules.check_schedule(line("DOT 3", "brake_fluid_replacement"), v2, P, BILL_DATE).category == "supported"

    def test_inspect_action_asks_for_finding(self):
        f = rules.check_schedule(line("v belt", "drive_belt_replacement"), vehicle(), P, BILL_DATE)
        assert f.category == "needs_confirmation" and "inspection" in f.label

    def test_secondary_source_only_is_unable(self):
        f = rules.check_schedule(line("gear oil", "transmission_oil_replacement"), vehicle(), P, BILL_DATE)
        assert f.category == "unable_to_verify" and f.evidence and not f.evidence[0].sufficient

    def test_add_on_not_in_complete_schedule(self):
        f = rules.check_schedule(line("ENG FLUSH", "engine_flush"), vehicle(), P, BILL_DATE)
        assert f.category == "needs_confirmation"
        assert f.label == "Not listed in the official service schedule: ask why it's recommended"
        # evidence is the official schedule itself, not some other item's interval
        assert len(f.evidence) == 1 and f.evidence[0].sufficient and f.evidence[0].source_rank == 1
        assert "Engine flush is not among them" in f.evidence[0].data["note"]

    def test_absence_needs_official_schedule(self):
        p = P.model_copy(deep=True)
        p.facts = [x for x in p.facts if x.kind != "schedule"]
        f = rules.check_schedule(line("ENG FLUSH", "engine_flush"), vehicle(), p, BILL_DATE)
        assert f.category == "unable_to_verify"

    def test_consumable_not_applicable(self):
        f = rules.check_schedule(line("washer fluid", "washer_fluid"), vehicle(), P, BILL_DATE)
        assert f.category == "not_applicable"

    def test_wear_repair_not_applicable(self):
        f = rules.check_schedule(line("brake shoe", "brake_shoe_replacement"), vehicle(), P, BILL_DATE)
        assert f.category == "not_applicable"

    def test_unsupported_vehicle(self):
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), vehicle(vehicle_id=None), None, BILL_DATE)
        assert f.category == "unable_to_verify"

    def test_variant_restricted_fact_with_unknown_variant(self):
        p = P.model_copy(deep=True)
        for fct in p.facts:
            if fct.data.get("job") == "air_filter_replacement":
                fct.applies_to = {"variants": ["STD"]}
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), vehicle(variant=None), p, BILL_DATE)
        assert f.category == "unable_to_verify" and "variant" in (f.uncertainty or "")

    def test_conflicting_official_sources(self):
        p = P.model_copy(deep=True)
        extra = next(x for x in p.facts if x.data.get("job") == "air_filter_replacement").model_copy(deep=True)
        extra.data["interval_km"] = 10000
        p.facts.append(extra)
        f = rules.check_schedule(line("air filter", "air_filter_replacement"), vehicle(), p, BILL_DATE)
        assert f.category == "unable_to_verify" and "disagree" in f.label


# ---------------------------------------------------------------- price

SELLERS = ["https://www.boodmo.com/a", "https://www.amazon.in/b", "https://www.flipkart.com/c"]


class TestPrice:
    def _line(self, unit, **kw):
        kw.setdefault("gst", True)
        return line("AIR FILTER", "air_filter_replacement", qty=1, unit=unit, total=unit, **kw)

    def test_within_range(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320, 340], SELLERS)]
        f = rules.check_price(self._line(330), comps, vehicle(), P, BILL_DATE)
        assert f.category == "supported" and "₹340" in f.calculation

    def test_above_threshold_needs_review_with_calculation(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320, 340], SELLERS)]
        f = rules.check_price(self._line(450), comps, vehicle(), P, BILL_DATE)
        assert f.category == "needs_confirmation"
        assert f.label.startswith("Price needs review against available listings")
        assert "₹391" in f.calculation  # 340 * 1.15

    def test_exactly_at_threshold_is_supported(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320, 400], SELLERS)]
        f = rules.check_price(self._line(460), comps, vehicle(), P, BILL_DATE)
        assert f.category == "supported"

    def test_threshold_is_configurable(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320, 400], SELLERS)]
        s = replace(settings, price_review_threshold_pct=5)
        assert rules.check_price(self._line(460), comps, vehicle(), P, BILL_DATE, s).category == "needs_confirmation"

    def test_fewer_than_three_comparables(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320], SELLERS)]
        f = rules.check_price(self._line(900), comps, vehicle(), P, BILL_DATE)
        assert f.category == "unable_to_verify" and f.label == "Unable to verify price fairly"

    def test_one_seller_only(self):
        comps = [price_fact(p, f"https://www.boodmo.com/{i}") for i, p in enumerate([300, 320, 340])]
        assert rules.check_price(self._line(900), comps, vehicle(), P, BILL_DATE).category == "unable_to_verify"

    def test_duplicates_dont_count(self):
        comps = [price_fact(300, SELLERS[0]), price_fact(300, SELLERS[0]), price_fact(320, SELLERS[1])]
        assert rules.check_price(self._line(900), comps, vehicle(), P, BILL_DATE).category == "unable_to_verify"

    def test_installed_price_not_compared_with_part_only(self):
        comps = [price_fact(p, u, listing="installed") for p, u in zip([300, 320, 340], SELLERS)]
        assert rules.check_price(self._line(900), comps, vehicle(), P, BILL_DATE).category == "unable_to_verify"

    def test_part_with_fitting_line_unable(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320, 340], SELLERS)]
        f = rules.check_price(self._line(900, kind="part_with_fitting"), comps, vehicle(), P, BILL_DATE)
        assert f.category == "unable_to_verify"

    def test_gst_normalised(self):
        # listings exclude GST at 28%: 300/320/340 excl -> 384/409.6/435.2 incl
        comps = [price_fact(p, u, gst_included=False) for p, u in zip([300, 320, 340], SELLERS)]
        assert rules.check_price(self._line(480), comps, vehicle(), P, BILL_DATE).category == "supported"
        assert rules.check_price(self._line(520), comps, vehicle(), P, BILL_DATE).category == "needs_confirmation"

    def test_unknown_tax_basis_on_listing_excluded(self):
        comps = [price_fact(p, u, gst_included=False, gst_rate=None) for p, u in zip([300, 320, 340], SELLERS)]
        assert rules.check_price(self._line(900), comps, vehicle(), P, BILL_DATE).category == "unable_to_verify"

    def test_unknown_tax_basis_on_bill_unable(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320, 340], SELLERS)]
        f = rules.check_price(self._line(900, gst=None), comps, vehicle(), P, BILL_DATE)
        assert f.category == "unable_to_verify" and "GST" in f.rule

    def test_document_tax_setting_used_when_line_has_none(self):
        comps = [price_fact(p, u) for p, u in zip([300, 320, 340], SELLERS)]
        d = doc([], prices_include_tax=True)
        f = rules.check_price(self._line(330, gst=None), comps, vehicle(), P, BILL_DATE, doc=d)
        assert f.category == "supported"

    def test_different_grade_excluded(self):
        comps = [price_fact(p, u, grade="aftermarket") for p, u in zip([100, 110, 120], SELLERS)]
        assert rules.check_price(self._line(330), comps, vehicle(), P, BILL_DATE).category == "unable_to_verify"

    def test_part_number_match_overrides_grade(self):
        comps = [price_fact(p, u, part_number="17210-K0N-D00", grade="unknown") for p, u in zip([300, 320, 340], SELLERS)]
        ln = self._line(330, part_number="17210K0ND00", grade="unknown")
        assert rules.check_price(ln, comps, vehicle(), P, BILL_DATE).category == "supported"

    def test_stale_or_untrusted_or_out_of_stock_excluded(self):
        comps = [price_fact(300, SELLERS[0], checked=date(2026, 1, 1)),
                 price_fact(320, "https://random-blog.example/x"),
                 price_fact(340, SELLERS[2], in_stock=False)]
        f = rules.check_price(self._line(900), comps, vehicle(), P, BILL_DATE)
        assert f.category == "unable_to_verify" and f.inputs["comparables_used"] == 0

    def test_labour_without_published_rate(self):
        f = rules.check_price(line("PMS labour", "periodic_service_labour", total=600, kind="labour"), [], vehicle(), P, BILL_DATE)
        assert f.category == "unable_to_verify"


# ---------------------------------------------------------------- coverage


class TestCoverage:
    def test_potential_recall_match(self):
        v = vehicle(mfg_month="2025-03")
        f = rules.check_coverage(line("front fork seal", "suspension_repair"), v, P, BILL_DATE)
        assert f.category == "needs_confirmation"
        assert f.label == "Potential recall match: verify eligibility using your chassis number"
        assert f.verify_url

    def test_recall_outside_period_falls_to_warranty(self):
        v = vehicle(mfg_month="2025-07")
        f = rules.check_coverage(line("front fork seal", "suspension_repair"), v, P, BILL_DATE)
        assert f.label == "Potential warranty coverage: verify with the manufacturer"
        assert "No matching recall found in the sources checked" in (f.uncertainty or "")

    def test_recall_needs_mfg_month(self):
        v = vehicle(mfg_month=None)
        f = rules.check_coverage(line("front fork seal", "suspension_repair"), v, P, BILL_DATE)
        assert "recall" not in f.label.lower()
        assert "manufacturing month is needed" in (f.uncertainty or "")

    def test_warranty_excluded_job_not_applicable(self):
        f = rules.check_coverage(line("air filter", "air_filter_replacement"), vehicle(), P, BILL_DATE)
        assert f.category == "not_applicable"

    def test_warranty_missing_purchase_date_unable(self):
        f = rules.check_coverage(line("bearing", "bearing_replacement"), vehicle(purchase_date=None), P, BILL_DATE)
        assert f.category == "unable_to_verify" and f.label == "Warranty eligibility could not be verified"

    def test_out_of_warranty_by_km(self):
        f = rules.check_coverage(line("bearing", "bearing_replacement"), vehicle(odometer_km=31000), P, BILL_DATE)
        assert f.category == "supported" and "by km" in f.label

    def test_never_says_free_or_covered(self):
        for v in (vehicle(mfg_month="2025-03"), vehicle()):
            f = rules.check_coverage(line("fork", "suspension_repair"), v, P, BILL_DATE)
            assert "free" not in f.label.lower() and "covered" not in f.label.lower().replace("coverage", "")

    def test_unsupported_vehicle(self):
        f = rules.check_coverage(line("fork", "suspension_repair"), vehicle(), None, BILL_DATE)
        assert f.category == "unable_to_verify"


# ---------------------------------------------------------------- authorization


class TestAuthorization:
    est = doc([line("Air cleaner element", "air_filter_replacement", total=400)])

    def test_no_estimate_not_applicable(self):
        assert rules.check_authorization(line("x", "air_filter_replacement"), None, []).category == "not_applicable"

    def test_on_estimate(self):
        f = rules.check_authorization(line("AIR FILTER", "air_filter_replacement", total=410), self.est, [])
        assert f.category == "supported" and f.label == "On the estimate"

    def test_on_estimate_but_higher(self):
        f = rules.check_authorization(line("AIR FILTER", "air_filter_replacement", total=600), self.est, [])
        assert f.category == "needs_confirmation" and "more than estimated" in f.label

    def test_approved_later(self):
        notes = [ApprovalNote(item="brake shoes", job="brake_shoe_replacement", how="phone call", on=date(2026, 10, 20))]
        f = rules.check_authorization(line("BRAKE SHOE", "brake_shoe_replacement", total=350), self.est, notes)
        assert f.category == "supported" and f.label == "Approved later (recorded by you)"

    def test_absent(self):
        f = rules.check_authorization(line("ENG FLUSH", "engine_flush", total=450), self.est, [])
        assert f.category == "needs_confirmation"
        assert f.label == "Not present in the uploaded estimate: confirm authorization"

    def test_unrecognised(self):
        f = rules.check_authorization(line("XYZ-99 SPL TRTMT", "unrecognised", total=450), self.est, [])
        assert f.category == "unable_to_verify"


# ---------------------------------------------------------------- arithmetic


class TestArithmetic:
    def test_line_ok_and_rounding(self):
        assert rules.check_line_arithmetic(line(qty=2, unit=112.5, total=225.4)).category == "supported"

    def test_line_mismatch(self):
        f = rules.check_line_arithmetic(line(qty=2, unit=112.5, total=250))
        assert f.category == "needs_confirmation"

    def test_only_amount_printed(self):
        assert rules.check_line_arithmetic(line(total=250)).category == "not_applicable"

    def _lines(self):
        return [line("a", total=1000), line("b", total=500)]

    def test_exclusive_tax_no_discount(self):
        d = doc(self._lines(), subtotal_inr=1500, prices_include_tax=False,
                tax_lines=[{"name": "CGST", "rate_pct": 9, "amount_inr": 135}, {"name": "SGST", "rate_pct": 9, "amount_inr": 135}],
                grand_total_inr=1770)
        assert [f.category for f in rules.check_bill_arithmetic(d)] == ["supported", "supported"]

    def test_discount_before_tax(self):
        d = doc(self._lines(), subtotal_inr=1500, discount_inr=100, discount_applied="before_tax", prices_include_tax=False,
                tax_lines=[{"name": "CGST", "amount_inr": 126}, {"name": "SGST", "amount_inr": 126}], grand_total_inr=1652)
        assert rules.check_bill_arithmetic(d)[-1].category == "supported"

    def test_discount_after_tax(self):
        d = doc(self._lines(), subtotal_inr=1500, discount_inr=100, discount_applied="after_tax", prices_include_tax=False,
                tax_lines=[{"name": "IGST", "amount_inr": 270}], grand_total_inr=1670)
        assert rules.check_bill_arithmetic(d)[-1].category == "supported"

    def test_tax_inclusive_prices(self):
        d = doc(self._lines(), prices_include_tax=True, tax_lines=[{"name": "GST", "amount_inr": 228.81}],
                round_off_inr=0, grand_total_inr=1500)
        assert rules.check_bill_arithmetic(d)[-1].category == "supported"

    def test_round_off_within_tolerance(self):
        d = doc(self._lines(), prices_include_tax=False, tax_lines=[{"name": "GST", "amount_inr": 270.4}],
                round_off_inr=-0.4, grand_total_inr=1770)
        assert rules.check_bill_arithmetic(d)[-1].category == "supported"

    def test_real_mismatch(self):
        d = doc(self._lines(), prices_include_tax=False, tax_lines=[{"name": "GST", "amount_inr": 270}], grand_total_inr=1900)
        f = rules.check_bill_arithmetic(d)[-1]
        assert f.category == "needs_confirmation" and "₹130" in f.label

    def test_ambiguous_tax_is_unable_not_mismatch(self):
        d = doc(self._lines(), tax_lines=[{"name": "GST", "amount_inr": 270}], grand_total_inr=1900)
        assert rules.check_bill_arithmetic(d)[-1].category == "unable_to_verify"

    def test_ambiguous_discount_is_unable(self):
        d = doc(self._lines(), discount_inr=100, prices_include_tax=False,
                tax_lines=[{"name": "GST", "amount_inr": 252}], grand_total_inr=1652)
        assert rules.check_bill_arithmetic(d)[-1].category == "unable_to_verify"

    def test_subtotal_mismatch(self):
        d = doc(self._lines(), subtotal_inr=1600, grand_total_inr=1600, prices_include_tax=True)
        assert rules.check_bill_arithmetic(d)[0].category == "needs_confirmation"

    def test_extracted_values_never_changed(self):
        d = doc(self._lines(), prices_include_tax=False, tax_lines=[{"name": "GST", "amount_inr": 270}], grand_total_inr=1900)
        before = d.model_dump()
        rules.check_bill_arithmetic(d)
        assert d.model_dump() == before


# ---------------------------------------------------------------- unrecognised + wording


def test_unrecognised_line_every_check_unable():
    fs = rules.unrecognised_findings()
    assert {f.check for f in fs} == {"schedule", "price", "coverage", "authorization"}
    assert all(f.category == "unable_to_verify" for f in fs)
    assert all("ask the advisor what this item is" in f.label for f in fs)


def test_unrecognised_line_runs_through_every_check():
    ln = line("SPL TRTMT XQ", "unrecognised", total=500)
    assert rules.check_schedule(ln, vehicle(), P, BILL_DATE).category == "unable_to_verify"
    assert rules.check_price(ln, [], vehicle(), P, BILL_DATE).category == "unable_to_verify"
    assert rules.check_coverage(ln, vehicle(), P, BILL_DATE).category == "unable_to_verify"


def _all_labels():
    v = vehicle(history=[hist("air_filter_replacement", "never")])
    findings = []
    for job in ("air_filter_replacement", "engine_flush", "washer_fluid", "brake_shoe_replacement",
                "drive_belt_replacement", "transmission_oil_replacement", "suspension_repair", "bearing_replacement"):
        ln = line(job, job, qty=1, unit=900, total=900, gst=True)
        findings += [rules.check_schedule(ln, v, P, BILL_DATE),
                     rules.check_price(ln, [price_fact(p, u) for p, u in zip([300, 320, 340], SELLERS)], v, P, BILL_DATE),
                     rules.check_coverage(ln, vehicle(mfg_month="2025-03"), P, BILL_DATE),
                     rules.check_authorization(ln, TestAuthorization.est, [])]
    findings += rules.unrecognised_findings()
    return findings


def test_no_rule_label_is_accusatory():
    for f in _all_labels():
        for text in (f.label, f.next_step or "", f.uncertainty or ""):
            assert not accusatory_terms(text), (f.label, text)


def test_all_four_categories_produced():
    assert {f.category for f in _all_labels()} == {"supported", "needs_confirmation", "unable_to_verify", "not_applicable"}


@pytest.mark.parametrize("text,bad", [
    ("Could you share the inspection finding for the engine flush?", False),
    ("You were overcharged for this", True),
    ("This was an unnecessary repair", True),
    ("This looks like a scam", True),
    ("Kindly explain this item", False),
])
def test_guard(text, bad):
    assert bool(accusatory_terms(text)) == bad
