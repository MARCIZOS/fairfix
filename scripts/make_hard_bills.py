"""Generate the hard sample bills in eval/ (images + answer keys).

  python scripts/make_hard_bills.py

Dev-only: needs `pip install playwright pillow` and `playwright install chromium`.
The generated files are committed, so you don't need to run this to evaluate.

Every bill is from a fictional workshop, is stamped SAMPLE, and prints the
same fake personal details (name, phone, registration) so the evaluation can
check they are removed. Each case's answer key is written by hand below:
- expected_fields: what is printed on the bill (for scoring extraction)
- expected:        the correct category for each check on each line
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "scripts" / "assets" / "fonts"
IMG = ROOT / "eval" / "images"
CASES = ROOT / "eval" / "cases"
TMP = ROOT / "eval" / "images" / "_render"

PERSONAL = {"name": "Test Customer", "phone": "9000000000", "reg": "UP14ZZ0000"}
MUST_NOT = [PERSONAL["phone"], PERSONAL["reg"], PERSONAL["name"]]
S, N, U, A = "supported", "needs_confirmation", "unable_to_verify", "not_applicable"
SAMPLE = {"vehicle_id": "fairfix_sample_scooter", "make": "FairFix", "model": "Sample Scooter",
          "variant": "STD", "fuel": "petrol", "city": "Ghaziabad"}


def L(text, qty, rate, amount, job, cats, kind="part", grade="unknown", part_number=None):
    """A bill line: what's printed, and the correct job and categories.
    cats = (schedule, price, coverage, authorization, arithmetic)."""
    return dict(text=text, qty=qty, rate=rate, amount=amount, job=job, cats=cats, kind=kind,
                grade=grade, part_number=part_number)


def partial(arith=S, auth=A):
    return (U, U, U, auth, arith)


# --------------------------------------------------------------------------
# The eight cases
# --------------------------------------------------------------------------

CASES_SPEC = [
    dict(
        name="hard_multipage_car",
        title="Two-page car invoice with decimal quantities",
        style="invoice", pages=2, photo="mild",
        workshop="Sample Car Care (fictional)", vehicle_line="Maruti Suzuki Swift VXI",
        vehicle={"vehicle_id": "maruti_swift", "make": "Maruti Suzuki", "model": "Swift", "variant": "VXI",
                 "fuel": "petrol", "odometer_km": 40200, "purchase_date": "2022-03-10", "city": "Ghaziabad"},
        odometer="40,200 km", date="20/10/2026", iso_date="2026-10-20",
        lines=[
            L("ENGINE OIL 5W30 3.5L", 3.5, 520, 1820, "engine_oil_replacement", partial()),
            L("OIL FILTER", 1, 285, 285, "oil_filter_replacement", partial()),
            L("AIR FILTER", 1, 610, 610, "air_filter_replacement", partial()),
            L("CABIN FILTER", 1, 540, 540, "cabin_filter_replacement", partial()),
            L("SPARK PLUG (SET OF 4)", 4, 245, 980, "spark_plug_replacement", partial()),
            L("BRAKE PAD SET FRONT", 1, 1850, 1850, "brake_pad_replacement", partial()),
            L("WHEEL ALIGNMENT & BALANCING", 1, 650, 650, "wheel_alignment_balancing", partial(), kind="labour"),
            L("PERIODIC MAINTENANCE LABOUR", 1, 1200, 1200, "periodic_service_labour", partial(), kind="labour"),
        ],
        page_break=5,
        tax=dict(incl=False, lines=[("CGST @ 9%", 9, 714.15), ("SGST @ 9%", 9, 714.15)], subtotal=7935,
                 round_off=-0.30, total=9363.00, note="All prices are exclusive of GST."),
        expected_bill=[S, S],
    ),
    dict(
        name="hard_handwritten_addition",
        title="Printed bill with a handwritten extra line and corrected total",
        style="invoice", photo="mild", handwritten=dict(text="Coolant top up", amount=250, total=1190),
        workshop="Sample Motors Service Centre (fictional)", vehicle_line="FairFix Sample Scooter STD",
        vehicle={**SAMPLE, "odometer_km": 12300, "purchase_date": "2025-04-10", "mfg_month": "2025-03",
                 "history": [{"job": "engine_oil_replacement", "status": "replaced", "km": 6200, "on": "2026-04-12"},
                             {"job": "spark_plug_replacement", "status": "never"}]},
        odometer="12,300 km", date="20/10/2026", iso_date="2026-10-20",
        lines=[
            L("ENGINE OIL 10W30 0.8L", 1, 380, 380, "engine_oil_replacement", (S, U, A, S, S), grade="genuine"),
            L("SPARK PLUG", 1, 160, 160, "spark_plug_replacement", (S, U, A, S, S), grade="genuine"),
            L("PERIODIC SERVICE LABOUR", 1, 400, 400, "periodic_service_labour", (A, U, A, S, S), kind="labour"),
            L("Coolant top up", None, None, 250, "coolant_replacement", (N, U, A, N, A), kind="unknown"),
        ],
        tax=dict(incl=True, lines=[], subtotal=None, round_off=None, total=1190, printed_total=940,
                 note="Rates are inclusive of GST."),
        estimate=[("Engine oil", 380), ("Spark plug", 160), ("Periodic service labour", 400)],
        expected_bill=[A, S],
    ),
    dict(
        name="hard_thermal_receipt",
        title="Faded thermal receipt with shop codes",
        style="thermal", photo="crumpled",
        workshop="QUICK SERVE 2W (SAMPLE)", vehicle_line="ACTIVA",
        vehicle={"vehicle_id": "honda_activa", "make": "Honda", "model": "Activa", "fuel": "petrol",
                 "odometer_km": 15100, "purchase_date": "2023-06-01", "city": "Ghaziabad"},
        odometer="15100", date="20-10-26", iso_date="2026-10-20",
        lines=[
            L("ENG OIL 900ML", 1, 340, 340, "engine_oil_replacement", partial()),
            L("SPK PLG", 1, 150, 150, "spark_plug_replacement", partial()),
            L("AIR FLTR ELEMENT", 1, 310, 310, "air_filter_replacement", partial()),
            L("GEN SRVC LBR", 1, 350, 350, "periodic_service_labour", partial(), kind="labour"),
            L("WSH", 1, 80, 80, "washing", partial()),
        ],
        tax=dict(incl=True, lines=[("CGST 9% (INCL)", 9, 93.81), ("SGST 9% (INCL)", 9, 93.81)], subtotal=None,
                 round_off=None, total=1230.00, note="GST INCLUDED"),
        expected_bill=[A, S],
    ),
    dict(
        name="hard_hindi_items",
        title="Bill with Hindi item names",
        style="invoice", photo="mild", hindi=True,
        workshop="Sample Seva Service Centre (fictional)", vehicle_line="FairFix Sample Scooter STD",
        vehicle={**SAMPLE, "odometer_km": 6400, "purchase_date": "2026-03-15", "mfg_month": "2026-02",
                 "history": [{"job": "engine_oil_replacement", "status": "never"},
                             {"job": "air_filter_replacement", "status": "never"}]},
        odometer="6,400 km", date="20/10/2026", iso_date="2026-10-20",
        lines=[
            L("इंजन ऑयल", 1, 380, 380, "engine_oil_replacement", (S, U, A, A, S)),
            L("एयर फ़िल्टर", 1, 320, 320, "air_filter_replacement", (N, U, A, A, S)),
            L("ब्रेक शू (पीछे)", 1, 350, 350, "brake_shoe_replacement", (A, U, A, A, S)),
            L("सर्विस चार्ज", 1, 400, 400, "periodic_service_labour", (A, U, A, A, S), kind="labour"),
            L("धुलाई", 1, 80, 80, "washing", (A, U, A, A, S)),
        ],
        tax=dict(incl=False, lines=[("CGST @ 9%", 9, 137.70), ("SGST @ 9%", 9, 137.70)], subtotal=1530,
                 round_off=-0.40, total=1805.00, note="Prices are exclusive of GST."),
        expected_bill=[S, S],
    ),
    dict(
        name="hard_arithmetic_errors",
        title="Bill whose line and grand total don't add up",
        style="invoice", photo="mild",
        workshop="Sample Motors Service Centre (fictional)", vehicle_line="FairFix Sample Scooter STD",
        vehicle={**SAMPLE, "odometer_km": 8100, "purchase_date": "2025-08-20", "mfg_month": "2025-05",
                 "history": [{"job": "engine_oil_replacement", "status": "replaced", "km": 2000, "on": "2026-02-01"}]},
        odometer="8,100 km", date="20/10/2026", iso_date="2026-10-20",
        lines=[
            L("ENGINE OIL 10W30 0.8L", 1, 380, 380, "engine_oil_replacement", (S, U, A, A, S), grade="genuine"),
            L("FRONT FORK OIL SEAL", 2, 240, 520, "suspension_repair", (A, U, N, A, N), grade="genuine"),
            L("BEARING (FRONT WHEEL)", 1, 650, 650, "bearing_replacement", (A, U, N, A, S), grade="genuine"),
            L("PERIODIC SERVICE LABOUR", 1, 400, 400, "periodic_service_labour", (A, U, A, A, S), kind="labour"),
        ],
        tax=dict(incl=False, lines=[("CGST @ 9%", 9, 175.50), ("SGST @ 9%", 9, 175.50)], subtotal=1950,
                 round_off=None, total=2401.00, note="Prices are exclusive of GST."),
        expected_bill=[S, N],
    ),
    dict(
        name="hard_estimate_vs_bill",
        title="Bill with items not on the estimate and higher labour",
        style="invoice", photo="mild", estimate_image=True,
        workshop="Sample Motors Service Centre (fictional)", vehicle_line="FairFix Sample Scooter STD",
        vehicle={**SAMPLE, "odometer_km": 12050, "purchase_date": "2025-09-01", "mfg_month": "2025-08",
                 "history": [{"job": "engine_oil_replacement", "status": "replaced", "km": 6000, "on": "2026-04-20"},
                             {"job": "air_filter_replacement", "status": "never"}]},
        odometer="12,050 km", date="20/10/2026", iso_date="2026-10-20",
        lines=[
            L("ENGINE OIL 10W30 0.8L", 1, 380, 380, "engine_oil_replacement", (S, U, A, S, S), grade="genuine"),
            L("AIR CLNR ELEMENT", 1, 320, 320, "air_filter_replacement", (S, U, A, S, S), grade="genuine"),
            L("PERIODIC SERVICE LABOUR", 1, 550, 550, "periodic_service_labour", (A, U, A, N, S), kind="labour"),
            L("ENGINE FLUSH", 1, 450, 450, "engine_flush", (N, U, A, N, S)),
            L("FUEL INJECTOR CLEANER", 1, 399, 399, "fuel_additive", (N, U, A, N, S)),
            L("BRAKE SHOE SET (REAR)", 1, 350, 350, "brake_shoe_replacement", (A, U, A, S, S), grade="genuine"),
        ],
        tax=dict(incl=False, lines=[("CGST @ 9%", 9, 220.41), ("SGST @ 9%", 9, 220.41)], subtotal=2449,
                 round_off=0.18, total=2890.00, note="Prices are exclusive of GST."),
        estimate=[("Engine oil", 380), ("Air filter", 320), ("Periodic service labour", 400)],
        approvals=[{"on": "2026-10-20", "item": "rear brake shoes", "job": "brake_shoe_replacement",
                    "how": "phone call with the advisor", "amount_inr": 350}],
        expected_bill=[S, S],
    ),
    dict(
        name="hard_dealer_gst_invoice",
        title="Dealer GST invoice with many columns and a discount",
        style="dealer", photo="mild",
        workshop="Sample Two Wheels Pvt Ltd (fictional dealer)", vehicle_line="HONDA ACTIVA 6G",
        vehicle={"vehicle_id": "honda_activa", "make": "Honda", "model": "Activa", "variant": "6G", "fuel": "petrol",
                 "odometer_km": 18400, "purchase_date": "2022-11-05", "city": "Ghaziabad"},
        odometer="18,400", date="20.10.2026", iso_date="2026-10-20",
        lines=[
            L("AIR FILTER ELEMENT", 1, 330, 330, "air_filter_replacement", partial(), grade="genuine", part_number="SAMPLE-AF-001"),
            L("SPARK PLUG", 1, 165, 165, "spark_plug_replacement", partial(), grade="genuine", part_number="SAMPLE-SP-002"),
            L("ENGINE OIL 0.9L", 1, 360, 360, "engine_oil_replacement", partial(), grade="genuine", part_number="SAMPLE-EO-003"),
            L("BRAKE SHOE SET", 1, 410, 410, "brake_shoe_replacement", partial(), grade="genuine", part_number="SAMPLE-BS-004"),
        ],
        hsn=["8421", "8511", "2710", "8708"],
        tax=dict(incl=True, lines=[("CGST 9%", 9, 96.48), ("SGST 9%", 9, 96.49)], subtotal=1265.00, taxable=1072.03,
                 discount=63.25, round_off=0.25, total=1202.00, note="MRP inclusive of all taxes. Discount on invoice total."),
        expected_bill=[S, S],
    ),
    dict(
        name="hard_damaged_photo",
        title="Dark, blurry photo with a stain hiding one amount",
        style="invoice", photo="bad", stain_row=2,
        workshop="Sample Motors Service Centre (fictional)", vehicle_line="FairFix Sample Scooter STD",
        vehicle={**SAMPLE, "odometer_km": 6300, "purchase_date": "2026-04-01", "mfg_month": "2026-03",
                 "history": [{"job": "engine_oil_replacement", "status": "never"},
                             {"job": "air_filter_replacement", "status": "never"}]},
        odometer="6,300 km", date="20/10/2026", iso_date="2026-10-20",
        lines=[
            L("ENGINE OIL 10W30 0.8L", 1, 380, 380, "engine_oil_replacement", (S, U, A, A, S), grade="genuine"),
            L("AIR CLNR ELEMENT", 1, None, None, "air_filter_replacement", (N, U, A, A, U), grade="genuine"),
            L("PERIODIC SERVICE LABOUR", 1, 400, 400, "periodic_service_labour", (A, U, A, A, S), kind="labour"),
        ],
        printed_hidden={1: (320, 320)},     # printed but covered by the stain
        tax=dict(incl=True, lines=[], subtotal=None, round_off=None, total=1100, note="Rates are inclusive of GST."),
        expected_bill=[A, U],
    ),
]


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def money(x):
    return "" if x is None else f"{x:,.2f}"


FONT_CSS = f"""
@font-face {{ font-family: Kalam; src: url('{(FONTS / 'kalam-latin-400-normal.woff2').as_uri()}'); }}
@font-face {{ font-family: Caveat; src: url('{(FONTS / 'caveat-latin-400-normal.woff2').as_uri()}'); }}
@font-face {{ font-family: Deva; src: url('{(FONTS / 'noto-sans-devanagari-devanagari-400-normal.woff2').as_uri()}'); }}
@font-face {{ font-family: Deva; font-weight: 600; src: url('{(FONTS / 'noto-sans-devanagari-devanagari-600-normal.woff2').as_uri()}'); }}
"""

BASE_CSS = """
body{margin:0;background:#fff;color:#141414;font-family:"DejaVu Sans",Arial,"Deva",sans-serif}
.sheet{width:820px;padding:26px 32px;position:relative;background:#fff}
.wm{position:absolute;top:42%;left:12%;font:700 86px "DejaVu Sans",Arial;color:rgba(200,0,0,.12);transform:rotate(-24deg);letter-spacing:6px;pointer-events:none}
.hd{display:flex;justify-content:space-between;border-bottom:2px solid #111;padding-bottom:8px}
.hd h1{margin:0;font-size:22px}.hd p{margin:2px 0;font-size:12px}
.meta{display:grid;grid-template-columns:1fr 1fr;font-size:13px;gap:2px 20px;border:1px solid #111;padding:8px;margin-top:8px}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
th,td{border:1px solid #111;padding:5px 6px;position:relative}th{background:#eee}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.tot{width:52%;margin-left:48%}.tot td{font-weight:600}
.ft{font-size:11px;margin-top:12px;display:flex;justify-content:space-between}
.strike{text-decoration:line-through;text-decoration-thickness:2px;color:#444}
.hw{font-family:Kalam,Caveat,cursive;color:#1b3a9a;font-size:19px}
.stain{position:absolute;z-index:2;border-radius:46% 54% 50% 50%/60% 45% 55% 40%;background:radial-gradient(ellipse at 50% 50%,rgba(96,60,18,1) 0 62%,rgba(120,80,35,.85) 78%,rgba(150,110,60,0) 100%)}
.thermal{width:330px;padding:16px 14px;font:13px/1.35 "DejaVu Sans Mono",monospace;color:#3a3a3a;background:#fbfaf6}
.thermal h1{font-size:16px;text-align:center;margin:0}.thermal .c{text-align:center}
.thermal .row{display:flex;justify-content:space-between}
.thermal hr{border:0;border-top:1px dashed #888}
.dealer table{font-size:11.5px}.dealer th{font-size:11px}
"""


def page_html(spec, lines, page_no, last_page, kind="bill"):
    is_est = kind == "estimate"
    title = "ESTIMATE" if is_est else ("TAX INVOICE" if spec["style"] != "thermal" else "CASH BILL")
    doc_no = ("EST" if is_est else "INV") + "-SAMPLE-" + spec["name"][5:15].upper().replace("_", "")
    if spec["style"] == "thermal":
        return thermal_html(spec, lines)
    hd = f"""<div class="hd"><div><h1>{spec['workshop']}</h1><p>Fictional workshop for software testing</p>
      <p>Plot 0, Test Nagar, Ghaziabad</p><p>GSTIN: SAMPLE-NOT-REAL</p></div>
      <div style="text-align:right"><p><b>{title}</b></p><p>No: {doc_no}</p><p>Date: {spec['date']}</p>
      {f'<p>Page {page_no} of {spec.get("pages", 1)}</p>' if spec.get("pages", 1) > 1 else ''}</div></div>"""
    meta = f"""<div class="meta"><div>Customer: {PERSONAL['name']}</div><div>Mobile: {PERSONAL['phone']}</div>
      <div>Vehicle: {spec['vehicle_line']}</div><div>Reg. No: {PERSONAL['reg']}</div>
      <div>Odometer: {spec['odometer']}</div><div>Job Card: JC-0000-SAMPLE</div></div>"""
    if page_no > 1:
        meta = f"<p style='font-size:12px'>Continued from page {page_no - 1}. Customer: {PERSONAL['name']}</p>"
    start = 0 if page_no == 1 else spec.get("page_break", 0)
    if spec["style"] == "dealer":
        head = "<tr><th>Sr</th><th>Part No.</th><th>Description</th><th>HSN</th><th>Qty</th><th>MRP (₹)</th><th>Taxable (₹)</th><th>GST %</th><th>Amount (₹)</th></tr>"
        rows = []
        for i, ln in enumerate(lines):
            taxable = ln["amount"] / 1.18
            rows.append(f"<tr><td>{start + i + 1}</td><td>{ln['part_number']}</td><td>{ln['text']}</td><td>{spec['hsn'][i]}</td>"
                        f"<td class=n>{ln['qty']}</td><td class=n>{money(ln['rate'])}</td><td class=n>{money(taxable)}</td>"
                        f"<td class=n>18</td><td class=n>{money(ln['amount'])}</td></tr>")
    else:
        hw_head = "<th>विवरण / Description</th>" if spec.get("hindi") else "<th>Description</th>"
        head = f"<tr><th>#</th>{hw_head}<th>Qty</th><th>Rate (₹)</th><th>Amount (₹)</th></tr>"
        rows = []
        for i, ln in enumerate(lines):
            idx = start + i
            if is_est:
                rows.append(f"<tr><td>{idx + 1}</td><td>{ln[0]}</td><td class=n>1</td><td class=n>{money(ln[1])}</td><td class=n>{money(ln[1])}</td></tr>")
                continue
            rate, amt = ln["rate"], ln["amount"]
            hidden = spec.get("printed_hidden", {}).get(idx)
            if hidden and not is_est:
                rate, amt = hidden
            stain = ""
            if spec.get("stain_row") == idx + 1 and not is_est:
                # anchored in the Rate cell, wide enough to also cover the Amount cell
                stain = "<div class='stain' style='left:-10px;top:-9px;width:236%;height:calc(100% + 18px)'></div>"
            if is_est:
                rows.append(f"<tr><td>{idx + 1}</td><td>{ln[0]}</td><td class=n>1</td><td class=n>{money(ln[1])}</td><td class=n>{money(ln[1])}</td></tr>")
                continue
            if ln["qty"] is None and spec.get("handwritten"):
                continue  # the handwritten line is drawn separately
            rows.append(f"<tr><td>{idx + 1}</td><td>{ln['text']}</td><td class=n>{'' if ln['qty'] is None else ln['qty']:}</td>"
                        f"<td class=n>{money(rate)}{stain}</td><td class=n>{money(amt)}</td></tr>")
        hw = spec.get("handwritten")
        if hw and not is_est:
            rows.append(f"<tr><td class=hw>{len(rows) + 1}</td><td class=hw>{hw['text']}</td><td></td><td></td>"
                        f"<td class='n hw'>{hw['amount']}/-</td></tr>")
    body = f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"
    tot = ""
    if is_est:
        tot = f"<table class=tot><tr><td>Estimated total (excl. GST)</td><td class=n>{money(sum(x[1] for x in lines))}</td></tr></table>"
    elif last_page:
        t = spec["tax"]
        trs = []
        if spec["style"] == "dealer":
            trs.append(f"<tr><td>Total taxable value</td><td class=n>{money(t['taxable'])}</td></tr>")
            trs += [f"<tr><td>{n}</td><td class=n>{money(a)}</td></tr>" for n, _r, a in t["lines"]]
            trs.append(f"<tr><td>Total (incl. GST)</td><td class=n>{money(t['subtotal'])}</td></tr>")
            trs.append(f"<tr><td>Less: Discount</td><td class=n>{money(t['discount'])}</td></tr>")
        else:
            if t.get("subtotal") is not None:
                trs.append(f"<tr><td>Sub Total</td><td class=n>{money(t['subtotal'])}</td></tr>")
            trs += [f"<tr><td>{n}</td><td class=n>{money(a)}</td></tr>" for n, _r, a in t["lines"]]
        if t.get("round_off") is not None:
            trs.append(f"<tr><td>Round Off</td><td class=n>{t['round_off']:+.2f}</td></tr>")
        hw = spec.get("handwritten")
        if hw:
            trs.append(f"<tr><td>Total</td><td class=n><span class=strike>{money(t['printed_total'])}</span>"
                       f" <span class=hw>{hw['total']}/-</span></td></tr>")
        else:
            trs.append(f"<tr><td>Grand Total</td><td class=n>{money(t['total'])}</td></tr>")
        tot = f"<table class=tot>{''.join(trs)}</table><p style='font-size:12px'>{t['note']}</p>"
    elif not last_page:
        tot = "<p style='font-size:12px;text-align:right'>Continued on next page...</p>"
    ft = "<div class=ft><span>Sample document generated to test FairFix. Not a real invoice.</span><span>Authorised signatory</span></div>"
    cls = "sheet dealer" if spec["style"] == "dealer" else "sheet"
    return f"<!doctype html><html><head><meta charset=utf-8><style>{FONT_CSS}{BASE_CSS}</style></head><body><div class='{cls}'><div class=wm>SAMPLE ONLY</div>{hd}{meta}{body}{tot}{ft}</div></body></html>"


def thermal_html(spec, lines):
    rows = "".join(
        f"<div>{ln['text']}</div><div class=row><span>  {ln['qty']} x {ln['rate']:.2f}</span><span>{ln['amount']:.2f}</span></div>"
        for ln in lines)
    t = spec["tax"]
    taxes = "".join(f"<div class=row><span>{n}</span><span>{a:.2f}</span></div>" for n, _r, a in t["lines"])
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{FONT_CSS}{BASE_CSS}</style></head><body>
    <div class="thermal sheet" style="width:330px"><div class=wm style="font-size:44px;left:4%">SAMPLE ONLY</div>
    <h1>{spec['workshop']}</h1><div class=c>Test Nagar, Ghaziabad</div><div class=c>GSTIN SAMPLE-NOT-REAL</div><hr>
    <div class=c>CASH BILL</div><div>BILL NO: TH-SAMPLE-0042</div><div>DATE: {spec['date']} 17:42</div>
    <div>CUST: {PERSONAL['name']}</div><div>MOB: {PERSONAL['phone']}</div><div>VEH: {spec['vehicle_line']} {PERSONAL['reg']}</div>
    <div>KM: {spec['odometer']}</div><hr>{rows}<hr>
    <div class=row><b>TOTAL</b><b>{t['total']:.2f}</b></div>{taxes}<div class=c>{t['note']}</div><hr>
    <div class=c>THANK YOU VISIT AGAIN</div><div class=c style="font-size:10px">SAMPLE - NOT A REAL BILL</div></div></body></html>"""


# --------------------------------------------------------------------------
# photo effects
# --------------------------------------------------------------------------

def photo(src: Path, dst: Path, how: str, seed: int):
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    rnd = random.Random(seed)
    im = Image.open(src).convert("RGB")
    if how == "crumpled":
        w, h = im.size
        over = Image.new("L", im.size, 0)
        d = ImageDraw.Draw(over)
        for _ in range(14):
            y = rnd.randint(0, h)
            d.line([(0, y), (w, y + rnd.randint(-60, 60))], fill=rnd.randint(25, 60), width=rnd.randint(2, 6))
        over = over.filter(ImageFilter.GaussianBlur(6))
        im = Image.composite(Image.new("RGB", im.size, (60, 55, 45)), im, over)
        im = ImageEnhance.Contrast(im).enhance(0.72)
    angle = {"mild": -3.0, "crumpled": 4.5, "bad": -7.5}[how] + rnd.uniform(-1, 1)
    bgc = (118, 96, 72)
    im = im.rotate(angle, expand=True, fillcolor=bgc, resample=Image.BICUBIC)
    bg = Image.new("RGB", (int(im.width * 1.1), int(im.height * 1.1)), bgc)
    bg.paste(im, ((bg.width - im.width) // 2, (bg.height - im.height) // 2))
    shade = Image.new("L", bg.size, 0)
    d = ImageDraw.Draw(shade)
    strength = {"mild": 90, "crumpled": 110, "bad": 160}[how]
    for i in range(bg.width):
        d.line([(i, 0), (i, bg.height)], fill=int(max(0, (i - bg.width * 0.5) / (bg.width * 0.5)) * strength))
    bg = Image.composite(Image.new("RGB", bg.size, (0, 0, 0)), bg, shade)
    blur = {"mild": 1.0, "crumpled": 1.3, "bad": 2.1}[how]
    bright = {"mild": 0.94, "crumpled": 0.9, "bad": 0.72}[how]
    bg = ImageEnhance.Brightness(bg).enhance(bright).filter(ImageFilter.GaussianBlur(blur))
    scale = {"mild": 2, "crumpled": 2, "bad": 2.4}[how]
    bg = bg.resize((int(bg.width / scale), int(bg.height / scale)))
    bg.save(dst, quality={"mild": 65, "crumpled": 58, "bad": 45}[how])


# --------------------------------------------------------------------------
# answer keys
# --------------------------------------------------------------------------

def typed_line(ln):
    return {"raw_text": ln["text"], "qty": ln["qty"], "unit_price_inr": ln["rate"], "line_total_inr": ln["amount"],
            "kind": ln["kind"], "grade": ln["grade"], "part_number": ln["part_number"]}


def case_json(spec, images):
    t = spec["tax"]
    bill = {"kind": "bill", "doc_date": spec["iso_date"], "odometer_km": spec["vehicle"].get("odometer_km"),
            "lines": [typed_line(x) for x in spec["lines"]],
            "subtotal_inr": t.get("subtotal"), "prices_include_tax": t["incl"],
            "tax_lines": [{"name": n, "rate_pct": r, "amount_inr": a} for n, r, a in t["lines"]],
            "discount_inr": t.get("discount"), "round_off_inr": t.get("round_off"), "grand_total_inr": t["total"]}
    est = None
    if spec.get("estimate"):
        est = {"kind": "estimate", "doc_date": spec["iso_date"],
               "lines": [{"raw_text": n, "line_total_inr": a} for n, a in spec["estimate"]]}
    fields = {"lines": [{"amount": x["amount"], "qty": x["qty"], **({"part_number": x["part_number"]} if x["part_number"] else {})}
                        for x in spec["lines"]],
              "grand_total": t["total"], "prices_include_tax": t["incl"], "must_not_contain": MUST_NOT}
    if t.get("subtotal") is not None:
        fields["subtotal"] = t["subtotal"]
    if t.get("discount") is not None:
        fields["discount"] = t["discount"]
    keys = ("schedule", "price", "coverage", "authorization", "arithmetic")
    return {
        "title": "Hard sample: " + spec["title"],
        "description": "Generated by scripts/make_hard_bills.py. Fictional workshop, marked SAMPLE; not a real bill.",
        "demo": False, "real": False, "source": "generated mock",
        "input": {"vehicle": spec["vehicle"], "bill": bill, "estimate": est, "approvals": spec.get("approvals", [])},
        "expected": {"lines": [{"job": x["job"], **dict(zip(keys, x["cats"]))} for x in spec["lines"]],
                     "bill": spec["expected_bill"]},
        "images": images,
        "expected_fields": fields,
    }


async def render_all():
    from playwright.async_api import async_playwright
    TMP.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 900, "height": 1000}, device_scale_factor=2)
        for n, spec in enumerate(CASES_SPEC):
            pages = spec.get("pages", 1)
            groups = [spec["lines"]] if pages == 1 else [spec["lines"][:spec["page_break"]], spec["lines"][spec["page_break"]:]]
            imgs = []
            for i, group in enumerate(groups, 1):
                html = TMP / f"{spec['name']}_{i}.html"
                html.write_text(page_html(spec, group, i, i == len(groups)), encoding="utf-8")
                await pg.goto(html.as_uri())
                await pg.wait_for_timeout(250)
                png = TMP / f"{spec['name']}_{i}.png"
                await pg.locator(".sheet").first.screenshot(path=str(png))
                out = IMG / (f"{spec['name']}.jpg" if pages == 1 else f"{spec['name']}_p{i}.jpg")
                photo(png, out, spec["photo"], seed=n * 10 + i)
                imgs.append(f"eval/images/{out.name}")
            images = {"bill": imgs}
            if spec.get("estimate_image"):
                html = TMP / f"{spec['name']}_est.html"
                html.write_text(page_html(spec, spec["estimate"], 1, True, kind="estimate"), encoding="utf-8")
                await pg.goto(html.as_uri())
                png = TMP / f"{spec['name']}_est.png"
                await pg.locator(".sheet").first.screenshot(path=str(png))
                out = IMG / f"{spec['name']}_estimate.jpg"
                photo(png, out, "mild", seed=n * 10 + 9)
                images["estimate"] = [f"eval/images/{out.name}"]
            (CASES / f"{spec['name']}.json").write_text(
                json.dumps(case_json(spec, images), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print("wrote", spec["name"], images)
        await b.close()
    for f in TMP.glob("*"):
        f.unlink()
    TMP.rmdir()


if __name__ == "__main__":
    asyncio.run(render_all())
