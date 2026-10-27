from __future__ import annotations

from datetime import date

from app.facts import get_pack
from app.schemas import Document, Fact, Field, JobHistory, LineItem, TaxLine, Vehicle

SAMPLE = "fairfix_sample_scooter"


def F(v, origin="extracted"):
    return Field(value=v, origin=origin, extracted_value=v)


def line(raw="item", job=None, qty=None, unit=None, total=None, kind="part", grade="genuine",
         part_number=None, gst=None) -> LineItem:
    return LineItem(raw_text=F(raw), qty=F(qty), unit_price_inr=F(unit), line_total_inr=F(total),
                    part_number=F(part_number), gst_included=F(gst), kind=kind, grade=grade, job=job,
                    job_source="user" if job else "none")


def vehicle(**kw) -> Vehicle:
    base = dict(vehicle_id=SAMPLE, make="FairFix", model="Sample Scooter", variant="STD", fuel="petrol",
                odometer_km=9800, purchase_date=date(2025, 8, 20), mfg_month="2025-07", city="Ghaziabad")
    base.update(kw)
    return Vehicle(**base)


def hist(job, status, km=None, on=None) -> JobHistory:
    return JobHistory(job=job, status=status, km=km, on=on)


def pack():
    return get_pack(SAMPLE)


def doc(lines, **kw) -> Document:
    d = Document(lines=lines)
    for k, v in kw.items():
        if k == "tax_lines":
            d.tax_lines = [TaxLine(**t) for t in v]
        else:
            setattr(d, k, F(v))
    return d


def price_fact(price, url, *, job="air_filter_replacement", grade="genuine", part_number=None,
               gst_included=True, gst_rate=28, listing="part_only", checked=date(2026, 10, 20),
               in_stock=True, delivers=True) -> Fact:
    return Fact(kind="part_price", source_url=url, excerpt=f"Price ₹{price}", checked_on=checked,
                source_rank=2,
                data=dict(job=job, grade=grade, part_number=part_number, price_inr=price,
                          gst_included=gst_included, gst_rate_pct=gst_rate, listing_type=listing,
                          in_stock=in_stock, delivers_to_city=delivers))


BILL_DATE = date(2026, 10, 20)   # sample scooter bought 2025-08-20 -> 14 months old
