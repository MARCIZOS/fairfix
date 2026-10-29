"""End-to-end live check of a FairFix install: settings, keys, models, search, every
endpoint, the full photo -> check -> message flow, access control and limits.

    python -m scripts.check_live            # writes logs/check_report.md

Uses your .env. Never prints key values. Makes real Nemotron and Tavily calls
(about 6 model calls and 1 search). Bill content is not written to the report:
only counts, timings and pass/fail.
"""

from __future__ import annotations

import sys
import time
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

from fastapi.testclient import TestClient  # noqa: E402

import tempfile  # noqa: E402

from app import auth, users, main as main_mod  # noqa: E402
from app import config as _cfg  # noqa: E402
from app.config import CREDIBLE_SELLER_DOMAINS  # noqa: E402

# Run against a throwaway accounts database so the check never adds users to the real one.
settings = replace(_cfg.settings, users_db=Path(tempfile.mkdtemp(prefix="fairfix-check-")) / "users.sqlite")
for _m in (auth, users, main_mod):
    _m.settings = settings

SECRET_FIELDS = {"nebius_api_key", "tavily_api_key", "signup_codes"}
results: list[tuple[str, str, str, str]] = []   # section, check, PASS/FAIL/WARN/INFO, detail


def rec(section: str, name: str, ok: bool | None, detail: str = "") -> None:
    status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
    results.append((section, name, status, detail))
    print(f"[{status}] {section}: {name}" + (f" - {detail}" if detail else ""), flush=True)


def warn(section: str, name: str, detail: str) -> None:
    results.append((section, name, "WARN", detail))
    print(f"[WARN] {section}: {name} - {detail}", flush=True)


def guarded(section: str, name: str):
    def deco(fn):
        try:
            fn()
        except Exception as exc:  # keep going; record the failure type only
            rec(section, name, False, f"{type(exc).__name__}: {str(exc)[:160]}")
        return fn
    return deco


client = TestClient(main_mod.app)
import secrets  # noqa: E402
CHECK_PW = "live-check-" + secrets.token_hex(6)
if settings.auth_required:   # sign-in is on: the live flow below runs as a throwaway account
    _r = client.post("/api/signup", json={"email": "live-check@example.com", "password": CHECK_PW,
                                          "invite_code": (settings.signup_codes or ("",))[0]})
    results.append(("Accounts", "check creates a throwaway account and signs in", "PASS" if _r.status_code == 200 else "FAIL", str(_r.status_code)))
    print(f"[{'PASS' if _r.status_code == 200 else 'FAIL'}] Accounts: check creates a throwaway account and signs in", flush=True)

# ------------------------------------------------------------------ 1. settings
S = "Settings"
for f in fields(settings):
    v = getattr(settings, f.name)
    if f.name in SECRET_FIELDS:
        shown = (f"{len(v)} set" if isinstance(v, tuple) else "set") if v else "not set"
    else:
        shown = str(v)
    rec(S, f.name, None, shown)
rec(S, "credible seller domains", None, f"{len(CREDIBLE_SELLER_DOMAINS)}: " + ", ".join(CREDIBLE_SELLER_DOMAINS))
rec(S, "NEBIUS_API_KEY present", settings.models_configured, "" if settings.models_configured else "add it to .env")
rec(S, "TAVILY_API_KEY present", settings.search_configured, "" if settings.search_configured else "price search will be skipped")
rec(S, "thresholds sane", 0 < settings.price_review_threshold_pct <= 100 and settings.price_min_comparables >= 1
    and settings.price_min_sellers >= 1 and 0 < settings.normalise_min_confidence <= 1 and settings.model_timeout_s > 0,
    f"price +{settings.price_review_threshold_pct}% over max of {settings.price_min_comparables} listings / {settings.price_min_sellers} sellers")
if not settings.auth_required:
    warn(S, "AUTH_REQUIRED", "0: anyone can check bills without an account. Set it to 1 before deploying.")
facts_ok = settings.facts_dir.exists() and any(settings.facts_dir.glob("*.json"))
rec(S, "fact packs folder", facts_ok, str(len(list(settings.facts_dir.glob("*.json")))) + " packs")

# ------------------------------------------------------------------ 2. models and search
M = "Models"


@guarded(M, "health")
def _health():
    t0 = time.perf_counter()
    h = client.get("/health").json()
    rec(M, "Token Factory reachable", bool(h.get("models_reachable")), f"{(time.perf_counter()-t0)*1000:.0f} ms" + (f", error {h.get('error')}" if h.get("error") else ""))
    for m, listed in (h.get("configured_models_listed") or {}).items():
        rec(M, f"model listed: {m}", listed)


@guarded(M, "text call")
def _text():
    from app.llm import call_text
    t0 = time.perf_counter()
    out = call_text("smoke_text", settings.model_text, [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=10)
    rec(M, f"text model answers ({settings.model_text})", "ok" in (out or "").lower(), f"{(time.perf_counter()-t0)*1000:.0f} ms")


@guarded(M, "price search")
def _search():
    if not settings.search_configured:
        warn(M, "Tavily", "skipped: no key")
        return
    from app.steps.prices import tavily_search
    t0 = time.perf_counter()
    res = tavily_search("Honda Activa air filter price India")
    doms = sorted({r.get("url", "").split("/")[2].replace("www.", "") for r in res if r.get("url", "").count("/") >= 2})
    rec(M, f"Tavily search ({settings.search_depth})", len(res) > 0, f"{len(res)} results in {(time.perf_counter()-t0)*1000:.0f} ms from {', '.join(doms[:6])}")


# ------------------------------------------------------------------ 3. endpoints
E = "Endpoints"
for path, test in [
    ("/", lambda r: "FairFix" in r.text), ("/dashboard.html", lambda r: "Evaluation report" in r.text),
    ("/app.js", lambda r: r.status_code == 200), ("/dashboard.js", lambda r: r.status_code == 200),
    ("/signin.html", lambda r: "auth-ui.js" in r.text), ("/samples/bill_english.jpg", lambda r: r.status_code == 200),
    ("/api/config", lambda r: "price_review_threshold_pct" in r.json() and "engines" in r.json()),
    ("/api/vehicles", lambda r: len(r.json()) >= 1), ("/api/jobs", lambda r: len(r.json()) >= 30),
    ("/api/demo", lambda r: len(r.json()) >= 1), ("/api/eval", lambda r: r.json()["current"] is not None),
    ("/api/stats", lambda r: "by_model" in r.json()), ("/api/session", lambda r: "required" in r.json()),
    ("/api/facts/fairfix_sample_scooter", lambda r: r.status_code == 200),
]:
    try:
        r = client.get(path)
        rec(E, f"GET {path}", r.status_code == 200 and test(r), f"{r.status_code}")
    except Exception as exc:
        rec(E, f"GET {path}", False, type(exc).__name__)
for d in client.get("/api/demo").json():
    r = client.get(f"/api/demo/{d['name']}")
    rep = r.json().get("report", {}) if r.status_code == 200 else {}
    rec(E, f"demo {d['name']}", r.status_code == 200 and len(rep.get("lines", [])) > 0,
        f"{len(rep.get('lines', []))} lines, ₹{rep.get('needs_confirmation_total_inr', 0)} to ask about")
h = client.get("/").headers
rec(E, "security headers", "frame-ancestors 'none'" in h.get("content-security-policy", "") and h.get("x-content-type-options") == "nosniff")
ev = client.get("/api/eval").json()["current"] or {}
rec(E, "evaluation release gate", ev.get("gate") == "PASS", f"{ev.get('run')}: fields {ev.get('fields_read', {}).get('ok')}/{ev.get('fields_read', {}).get('total')}")

# ------------------------------------------------------------------ 4. live flow
F = "Live flow"
STATE: dict = {}


@guarded(F, "read photo")
def _extract():
    if not settings.models_configured:
        warn(F, "read photo", "skipped: no Nebius key")
        return
    img = ROOT / "eval" / "images" / "sample_bill_photo.jpg"
    t0 = time.perf_counter()
    r = client.post("/api/extract", files={"bill": (img.name, img.read_bytes(), "image/jpeg")})
    ms = (time.perf_counter() - t0) * 1000
    ok = r.status_code == 200
    rec(F, "POST /api/extract (Nemotron VL reads the sample photo)", ok, f"{r.status_code} in {ms/1000:.1f} s" + ("" if ok else f": {r.text[:150]}"))
    if not ok:
        return
    out = r.json()
    bill = out["bill"]
    STATE["bill"] = bill
    gt = bill["grand_total_inr"]["value"]
    rec(F, "lines read", len(bill["lines"]) == 6, f"{len(bill['lines'])} lines (expected 6)")
    rec(F, "grand total read", gt is not None and abs(float(gt) - 2595) < 1, f"₹{gt} (expected ₹2,595)")
    import json as _j
    exp = [e["job"] for e in _j.loads((ROOT / "eval" / "cases" / "sample_scooter_service_photo.json").read_text(encoding="utf-8"))["expected"]["lines"]]
    got = [ln.get("job") for ln in bill["lines"]]
    right = sum(1 for a, b in zip(got, exp) if a == b)
    rec(F, "lines mapped to the expected job (the made-up item should stay unrecognised)", right == len(exp), f"{right} of {len(exp)}")
    priced = sum(1 for ln in bill["lines"] if ln.get("kind") in ("part", "consumable"))
    rec(F, "line types inferred for the price search", priced >= 4, f"{priced} part or consumable lines")
    rec(F, "no reading hints raised", not [f for f in out["flags"] if f.get("check") == "reading"], f"{len(out['flags'])} flags")
    import json as _json
    blob = _json.dumps(bill)
    rec(F, "personal data removed", not any(x in blob for x in ("9000000000", "UP14ZZ0000", "Test Customer")))
    rec(F, "trace step returned", out.get("trace", {}).get("engine", "").startswith("Nemotron"), out.get("trace", {}).get("engine", ""))


@guarded(F, "audit")
def _audit():
    if "bill" not in STATE:
        return
    import json as _json
    case = _json.loads((ROOT / "eval" / "cases" / "sample_scooter_service_photo.json").read_text(encoding="utf-8"))
    body = {"bill": STATE["bill"], "vehicle": case["input"]["vehicle"], "approvals": case["input"].get("approvals", []), "lang": "en"}
    t0 = time.perf_counter()
    r = client.post("/api/audit", json=body)
    ms = (time.perf_counter() - t0) * 1000
    rec(F, "POST /api/audit (rules + prices + message)", r.status_code == 200, f"{r.status_code} in {ms/1000:.1f} s")
    if r.status_code != 200:
        return
    rep = r.json()
    STATE["report"] = rep
    cats: dict[str, int] = {}
    for ln in rep["lines"]:
        for f in ln["findings"]:
            cats[f["category"]] = cats.get(f["category"], 0) + 1
    rec(F, "findings by category", None, ", ".join(f"{k} {v}" for k, v in sorted(cats.items())))
    rec(F, "every finding has rule + inputs", all(f.get("rule") for ln in rep["lines"] for f in ln["findings"]))
    rec(F, "message drafted by Nemotron", rep["message_source"] == "model", rep["message_source"])
    from app.guard import accusatory_terms
    bad = accusatory_terms(rep["message_to_advisor"])
    rec(F, "message passes the neutral-wording guard", not bad, ", ".join(bad))
    steps = {t["step"]: t for t in rep.get("trace", [])}
    rec(F, "pipeline trace", len(steps) >= 5, " · ".join(f"{k} {'skipped' if t['skipped'] else str(round(t['ms'])) + ' ms'}" for k, t in steps.items()))
    if settings.search_configured:
        det = steps.get("prices", {}).get("detail", "")
        rec(F, "price search ran", det.split(" ")[0].isdigit() and "for 0 parts" not in det, det)


@guarded(F, "hindi message")
def _hindi():
    if "report" not in STATE:
        return
    t0 = time.perf_counter()
    r = client.post("/api/message", json={"report": STATE["report"], "lang": "hi"})
    msg = r.json().get("message", "") if r.status_code == 200 else ""
    deva = sum(1 for ch in msg if "ऀ" <= ch <= "ॿ")
    rec(F, "Hindi message", r.status_code == 200 and deva > 20, f"{r.json().get('source')} in {(time.perf_counter()-t0):.1f} s, {deva} Devanagari characters")


@guarded(F, "price search speed")
def _price_speed():
    if not (settings.search_configured and settings.models_configured):
        return
    from concurrent.futures import ThreadPoolExecutor
    from app import cache
    from app.schemas import LineItem, Vehicle
    from app.steps import prices
    veh = Vehicle(vehicle_id="honda_activa", make="Honda", model="Activa 6G")
    lines = [LineItem(raw_text="AIR FILTER", job="air_filter_replacement", kind="part"),
             LineItem(raw_text="SPARK PLUG", job="spark_plug_replacement", kind="part"),
             LineItem(raw_text="ENGINE OIL 0.8L", job="engine_oil_replacement", kind="consumable")]
    real_get, real_put = cache.get_prices, cache.put_prices
    cache.get_prices, cache.put_prices = (lambda k: None), (lambda k, f: None)   # measure a cold search
    try:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=3) as pool:
            outs = list(pool.map(lambda ln: prices.lookup_comparables(ln, veh), lines))
        s = time.perf_counter() - t0
    finally:
        cache.get_prices, cache.put_prices = real_get, real_put
    n = sum(len(f) for f, _ in outs)
    rec(F, "price search for 3 parts, cold cache (timing depends on Nebius load)", None, f"{s:.1f} s, {n} listings found")


# ------------------------------------------------------------------ 5. access control
A = "Accounts"


@guarded(A, "account flow")
def _accounts():
    s = replace(settings, auth_required=True, rate_audit_per_hour=2, login_attempts=3,
                users_db=Path(tempfile.mkdtemp(prefix="fairfix-check-")) / "users.sqlite")
    for m in (auth, users, main_mod):
        m.settings = s
    auth.limiter.reset()
    try:
        c = TestClient(main_mod.app)
        pw, email = "check-flow-2026", "flow@example.com"
        demo = c.get("/api/demo/sample_scooter_service").json()["report"]
        rec(A, "samples open without an account", c.get("/api/demo/sample_scooter_service").status_code == 200)
        rec(A, "audit refused when signed out", c.post("/api/audit", json={"bill": {"kind": "bill"}, "vehicle": {"make": "x", "model": "y"}}).status_code == 401)
        rec(A, "message falls back to template when signed out", c.post("/api/message", json={"report": demo, "lang": "en"}).json()["source"] == "template")
        rec(A, "weak password refused", c.post("/api/signup", json={"email": email, "password": "password123"}).status_code == 400)
        r = c.post("/api/signup", json={"email": email, "password": pw, "invite_code": (s.signup_codes or ("",))[0]})
        sc = r.headers.get("set-cookie", "").lower()
        rec(A, "sign-up signs in (HttpOnly, SameSite=Lax)", r.status_code == 200 and "httponly" in sc and "samesite=lax" in sc)
        rec(A, "duplicate email refused", c.post("/api/signup", json={"email": email.upper(), "password": pw}).status_code in (400, 403))
        rec(A, "cross-site request refused", c.post("/api/audit", json={}, headers={"Origin": "https://evil.example"}).status_code == 403)
        c.post("/api/logout")
        rec(A, "sign out ends the session", c.get("/api/session").json()["signed_in"] is False)
        rec(A, "sign in with email and password", c.post("/api/login", json={"email": email, "password": pw}).status_code == 200)
        c.post("/api/logout")
        for _ in range(3):
            c.post("/api/login", json={"email": email, "password": "wrong-password"})
        rec(A, "lockout after repeated wrong passwords", c.post("/api/login", json={"email": email, "password": pw}).status_code == 429)
        auth.limiter.reset()
        c.post("/api/login", json={"email": email, "password": pw})
        rec(A, "delete account", c.post("/api/account/delete", json={"password": pw}).status_code == 200
            and c.post("/api/login", json={"email": email, "password": pw}).status_code == 401)
    finally:
        for m in (auth, users, main_mod):
            m.settings = settings
        auth.limiter.reset()


# ------------------------------------------------------------------ report
counts = {k: sum(1 for r in results if r[2] == k) for k in ("PASS", "FAIL", "WARN", "INFO")}
out = ["# FairFix live check", "", f"Run {datetime.now():%Y-%m-%d %H:%M} · {counts['PASS']} passed · {counts['FAIL']} failed · {counts['WARN']} warnings", ""]
section = None
for sec, name, st, detail in results:
    if sec != section:
        out += ["", f"## {sec}", "", "| Check | Result | Detail |", "|---|---|---|"]
        section = sec
    out.append(f"| {name} | {st} | {detail.replace('|', '/')} |")
path = ROOT / "logs" / "check_report.md"
path.parent.mkdir(exist_ok=True)
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['WARN']} warnings. Report: {path}")
sys.exit(1 if counts["FAIL"] else 0)
