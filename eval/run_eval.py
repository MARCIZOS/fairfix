"""Evaluate FairFix against the fixed test set in eval/cases/.

Usage
  python -m eval.run_eval                 # rules + alias mapping, no network (fast, repeatable)
  python -m eval.run_eval --models        # also use the model for line mapping and the message
  python -m eval.run_eval --extract       # also read case photos with the vision model and score fields

Writes eval/results.md with raw counts, every wrong verdict, the malformed-JSON
breakdown per step, and latency. Exits with code 1 if the release gate fails:
any unsupported flag (Needs confirmation where the expected answer differs) or
any accusatory wording in labels, next steps or the advisor message.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audit import run_audit  # noqa: E402
from app.config import settings  # noqa: E402
from app.guard import accusatory_terms  # noqa: E402
from app.schemas import ApprovalNote, Document, Vehicle  # noqa: E402

CHECKS = ("schedule", "price", "coverage", "authorization", "arithmetic")


def load_cases() -> list[tuple[str, dict]]:
    return [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in sorted((ROOT / "eval" / "cases").glob("*.json"))]


def score_fields(expected: dict, got: Document) -> tuple[int, int, list[str]]:
    """Compare extracted fields with expected_fields: line count, each line amount, totals."""
    ok = tot = 0
    misses: list[str] = []

    def cmp(name, want, have, tol=0.51):
        nonlocal ok, tot
        tot += 1
        good = (want is None and have is None) or (want is not None and have is not None and abs(float(want) - float(have)) <= tol)
        ok += good
        if not good:
            misses.append(f"{name}: expected {want}, got {have}")

    lines = expected.get("lines", [])
    tot += 1
    if len(lines) == len(got.lines):
        ok += 1
    else:
        misses.append(f"line count: expected {len(lines)}, got {len(got.lines)}")
    for i, want in enumerate(lines):
        have = got.lines[i] if i < len(got.lines) else None
        cmp(f"line {i + 1} amount", want.get("amount"), have.line_total_inr.value if have else None)
        if "qty" in want:
            cmp(f"line {i + 1} qty", want.get("qty"), have.qty.value if have else None, 0.001)
    for k, attr in (("subtotal", "subtotal_inr"), ("grand_total", "grand_total_inr"), ("discount", "discount_inr")):
        if k in expected:
            cmp(k, expected[k], getattr(got, attr).value)
    return ok, tot, misses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", action="store_true", help="use the text model for mapping and the message")
    ap.add_argument("--extract", action="store_true", help="read case photos with the vision model")
    args = ap.parse_args()
    if (args.models or args.extract) and not settings.models_configured:
        print("NEBIUS_API_KEY is not set; run without --models/--extract or configure .env")
        return 2

    log_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    per_check = {c: Counter() for c in CHECKS}
    wrong: list[str] = []
    unsupported_flags: list[str] = []
    wording_hits: list[str] = []
    job_ok = job_tot = 0
    bill_ok = bill_tot = 0
    field_ok = field_tot = 0
    field_misses: list[str] = []
    latencies: list[float] = []
    real = mock = 0

    for name, case in load_cases():
        inp = case["input"]
        real += bool(case.get("real"))
        mock += not case.get("real")
        bill = Document.model_validate(inp["bill"])
        est = Document.model_validate(inp["estimate"]) if inp.get("estimate") else None

        if args.extract and case.get("images"):
            from app.steps.extract import extract_document
            pages = [((ROOT / p).read_bytes(), "image/png" if p.endswith(".png") else "image/jpeg") for p in case["images"]["bill"]]
            got = extract_document(pages, "bill")
            o, t, m = score_fields(case.get("expected_fields", {}), got)
            field_ok += o; field_tot += t
            field_misses += [f"{name}: {x}" for x in m]

        t0 = time.perf_counter()
        report = run_audit(bill, Vehicle.model_validate(inp["vehicle"]), estimate=est,
                           approvals=[ApprovalNote.model_validate(a) for a in inp.get("approvals", [])],
                           use_models=args.models, use_search=False)
        latencies.append(time.perf_counter() - t0)

        exp = case.get("expected", {})
        for i, want in enumerate(exp.get("lines", [])):
            if i >= len(report.lines):
                wrong.append(f"{name} line {i + 1}: missing from report")
                continue
            lr = report.lines[i]
            job_tot += 1
            if lr.item.job == want.get("job"):
                job_ok += 1
            else:
                wrong.append(f"{name} line {i + 1} ({lr.item.raw_text.value}): job expected {want.get('job')}, got {lr.item.job}")
            got = {f.check: f for f in lr.findings}
            for c in CHECKS:
                if c not in want:
                    continue
                f = got.get(c)
                have = f.category if f else None
                per_check[c]["total"] += 1
                if have == want[c]:
                    per_check[c]["correct"] += 1
                else:
                    wrong.append(f"{name} line {i + 1} ({lr.item.raw_text.value}) {c}: expected {want[c]}, got {have}"
                                 + (f" - \"{f.label}\"" if f else ""))
                    if have == "needs_confirmation":
                        unsupported_flags.append(f"{name} line {i + 1} {c}: \"{f.label}\" (expected {want[c]})")
        for j, want in enumerate(exp.get("bill", [])):
            bill_tot += 1
            have = report.bill_findings[j].category if j < len(report.bill_findings) else None
            if have == want:
                bill_ok += 1
            else:
                wrong.append(f"{name} bill check {j + 1}: expected {want}, got {have}")
                if have == "needs_confirmation":
                    unsupported_flags.append(f"{name} bill check {j + 1}: \"{report.bill_findings[j].label}\"")

        texts = [report.message_to_advisor]
        for lr in report.lines:
            for f in lr.findings:
                texts += [f.label, f.next_step or ""]
        for f in report.bill_findings:
            texts += [f.label, f.next_step or ""]
        for t in texts:
            hits = accusatory_terms(t)
            if hits:
                wording_hits.append(f"{name}: {hits} in \"{t[:80]}\"")

    # malformed JSON breakdown from the call log written during this run
    calls = defaultdict(Counter)
    if settings.call_log.exists():
        for line in settings.call_log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ts", "") < log_start:
                continue
            st = calls[r["step"]]
            st[f"{r['outcome']}@{r['attempt']}"] += 1
    for st in calls.values():
        # A call = one first attempt. The client logs "ok" when the provider answers and
        # "malformed" when that answer fails the schema, so subtract to get clean results.
        st["calls"] = st["ok@1"] + st["timeout@1"] + st["provider_error@1"]
        st["failed_first"] = st["malformed@1"]
        st["ok_first"] = st["ok@1"] - st["malformed@1"]
        st["unusable"] = st["unusable@2"]
        st["retry_ok"] = st["ok@2"] - st["malformed@2"]
        st["timeout"] = st["timeout@1"] + st["timeout@2"]
        st["provider_error"] = st["provider_error@1"] + st["provider_error@2"]

    gate_ok = not unsupported_flags and not wording_hits
    pct = lambda o, t: f"{o} of {t} ({(100 * o / t):.0f}%)" if t else "n/a"
    out = [f"# FairFix evaluation results", "",
           f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')} · cases: {real + mock} ({real} real, {mock} mock) · "
           f"mode: {'models' if args.models else 'rules + alias mapping, no network'}"
           + (" + photo extraction" if args.extract else ""), "",
           "These are small-sample results for the listed cases only, not evidence of broad real-world reliability.", "",
           f"## Release gate: {'PASS' if gate_ok else 'FAIL'}", "",
           f"- Unsupported flags (Needs confirmation where expected differs): **{len(unsupported_flags)}**",
           f"- Accusatory wording in labels, next steps or messages: **{len(wording_hits)}**", ""]
    out += [f"  - {x}" for x in unsupported_flags + wording_hits]
    out += ["", "## Accuracy (raw counts)", "", "| Measure | Result |", "| --- | --- |",
            f"| Line mapped to the expected job | {pct(job_ok, job_tot)} |"]
    for c in CHECKS:
        out.append(f"| {c.capitalize()} category | {pct(per_check[c]['correct'], per_check[c]['total'])} |")
    out.append(f"| Bill-level arithmetic | {pct(bill_ok, bill_tot)} |")
    if args.extract:
        out.append(f"| Extracted fields correct (before correction) | {pct(field_ok, field_tot)} |")
    out += ["", "## Every wrong verdict (inspect each by hand)", ""]
    out += [f"- {w}" for w in wrong] or ["- None"]
    if field_misses:
        out += ["", "## Extraction misses", ""] + [f"- {m}" for m in field_misses]
    out += ["", "## Model calls (from logs/calls.jsonl, this run)", ""]
    if calls:
        out += ["| Step | Calls | OK first try | Failed first try | OK after retry | Still unusable | Timeouts | Provider errors |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for step, st in sorted(calls.items()):
            out.append(f"| {step} | {st['calls']} | {st['ok_first']} | {st['failed_first']} | {st['retry_ok']} | "
                       f"{st['unusable']} | {st['timeout']} | {st['provider_error']} |")
    else:
        out.append("No model calls in this run.")
    if latencies:
        out += ["", f"Latency per audit: median {statistics.median(latencies):.2f} s, slowest {max(latencies):.2f} s "
                    f"(search off; add price search time separately)."]
    (ROOT / "eval" / "results.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
