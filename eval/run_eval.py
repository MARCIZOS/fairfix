"""Evaluate FairFix against the fixed test set in eval/cases/.

Usage
  python -m eval.run_eval                       # rules + alias mapping, no network (fast, repeatable)
  python -m eval.run_eval --models              # also use Nemotron for line mapping and the message
  python -m eval.run_eval --models --extract    # also read every case photo with Nemotron, end to end
  python -m eval.run_eval --models --extract --only hard_   # just the cases whose name starts with hard_

Two tracks are reported:
- After correction: the audit runs on the correct readings (what the owner sees after
  the correction screen). This track decides the release gate.
- Straight from the photo (with --extract): the audit runs on exactly what Nemotron read,
  before any correction. Misreads show up here as wrong verdicts.

Writes eval/results.md. Exits 1 if the release gate fails: any unsupported flag (Needs
confirmation where the expected answer differs) after correction, or any accusatory wording.
"""

from __future__ import annotations

import argparse
import difflib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Windows consoles default to a code page without ₹; print UTF-8 instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
sys.path.insert(0, str(ROOT))

from app.audit import run_audit  # noqa: E402
from app.config import settings  # noqa: E402
from app.guard import accusatory_terms  # noqa: E402
from app.llm import ModelError  # noqa: E402
from app.schemas import ApprovalNote, Document, Vehicle  # noqa: E402

CHECKS = ("schedule", "price", "coverage", "authorization", "arithmetic")
MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def load_cases(only: str | None) -> list[tuple[str, dict]]:
    out = []
    for p in sorted((ROOT / "eval" / "cases").glob("*.json")):
        if only and not p.stem.startswith(only):
            continue
        out.append((p.stem, json.loads(p.read_text(encoding="utf-8"))))
    return out


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def align(expected_texts: list[str], got: Document) -> list[int | None]:
    """For each expected line, the index of the extracted line it matches (by text), or None."""
    used: set[int] = set()
    out: list[int | None] = []
    for want in expected_texts:
        best, best_score = None, 0.0
        for j, ln in enumerate(got.lines):
            if j in used:
                continue
            score = difflib.SequenceMatcher(None, _norm(want), _norm(ln.raw_text.value)).ratio()
            if score > best_score:
                best, best_score = j, score
        if best is not None and best_score >= 0.5:
            used.add(best)
            out.append(best)
        else:
            out.append(None)
    return out


def score_fields(expected: dict, texts: list[str], got: Document) -> tuple[int, int, list[str]]:
    """Compare what was read with what is printed: lines, amounts, quantities, part numbers, totals, tax basis, privacy."""
    ok = tot = 0
    misses: list[str] = []

    def check(name, good, detail):
        nonlocal ok, tot
        tot += 1
        ok += bool(good)
        if not good:
            misses.append(f"{name}: {detail}")

    def num_eq(want, have, tol):
        return (want is None and have is None) or (want is not None and have is not None and abs(float(want) - float(have)) <= tol)

    lines = expected.get("lines", [])
    check("line count", len(lines) == len(got.lines), f"expected {len(lines)}, got {len(got.lines)}")
    idx = align(texts, got)
    for i, want in enumerate(lines):
        j = idx[i]
        have = got.lines[j] if j is not None else None
        label = f"line {i + 1} ({texts[i]})"
        if have is None:
            check(f"{label} found", False, "not read at all")
            continue
        check(f"{label} amount", num_eq(want.get("amount"), have.line_total_inr.value, 0.51),
              f"expected {want.get('amount')}, got {have.line_total_inr.value}"
              + (" (printed value is hidden: it should be null, not guessed)" if want.get("amount") is None else ""))
        if "qty" in want:
            check(f"{label} qty", num_eq(want.get("qty"), have.qty.value, 0.001), f"expected {want.get('qty')}, got {have.qty.value}")
        if want.get("part_number"):
            check(f"{label} part number", _norm(want["part_number"]).replace("-", "") == _norm(have.part_number.value).replace("-", ""),
                  f"expected {want['part_number']}, got {have.part_number.value}")
    for k, attr in (("subtotal", "subtotal_inr"), ("grand_total", "grand_total_inr"), ("discount", "discount_inr")):
        if k in expected:
            check(k, num_eq(expected[k], getattr(got, attr).value, 0.51), f"expected {expected[k]}, got {getattr(got, attr).value}")
    if "prices_include_tax" in expected:
        check("prices include GST", expected["prices_include_tax"] == got.prices_include_tax.value,
              f"expected {expected['prices_include_tax']}, got {got.prices_include_tax.value}")
    dumped = got.model_dump_json().lower()
    for secret in expected.get("must_not_contain", []):
        check("privacy", secret.lower() not in dumped, f"PERSONAL DATA KEPT: '{secret}' appears in the extracted data")
    return ok, tot, misses


class Track:
    """Accumulates category accuracy for one way of feeding the audit."""

    def __init__(self, name: str):
        self.name = name
        self.per_check = {c: Counter() for c in CHECKS}
        self.job = Counter()
        self.bill = Counter()
        self.wrong: list[str] = []
        self.flags: list[str] = []
        self.wording: list[str] = []
        self.latencies: list[float] = []

    def score(self, case_name: str, report, exp: dict, texts: list[str], extracted: Document | None = None):
        idx = list(range(len(report.lines)))
        if extracted is not None:
            idx = align(texts, extracted)
        for i, want in enumerate(exp.get("lines", [])):
            j = idx[i] if i < len(idx) else None
            if j is None or j >= len(report.lines):
                self.wrong.append(f"{case_name} line {i + 1} ({texts[i]}): not in the report (line not read)")
                for c in CHECKS:
                    if c in want:
                        self.per_check[c]["total"] += 1
                self.job["total"] += 1
                continue
            lr = report.lines[j]
            self.job["total"] += 1
            if lr.item.job == want.get("job"):
                self.job["correct"] += 1
            else:
                self.wrong.append(f"{case_name} line {i + 1} ({lr.item.raw_text.value}): job expected {want.get('job')}, got {lr.item.job}")
            got = {f.check: f for f in lr.findings}
            for c in CHECKS:
                if c not in want:
                    continue
                f = got.get(c)
                have = f.category if f else None
                self.per_check[c]["total"] += 1
                if have == want[c]:
                    self.per_check[c]["correct"] += 1
                else:
                    self.wrong.append(f"{case_name} line {i + 1} ({lr.item.raw_text.value}) {c}: expected {want[c]}, got {have}"
                                      + (f" - \"{f.label}\"" if f else ""))
                    if have == "needs_confirmation":
                        self.flags.append(f"{case_name} line {i + 1} {c}: \"{f.label}\" (expected {want[c]})")
        for j, want in enumerate(exp.get("bill", [])):
            self.bill["total"] += 1
            have = report.bill_findings[j].category if j < len(report.bill_findings) else None
            if have == want:
                self.bill["correct"] += 1
            else:
                f = report.bill_findings[j] if j < len(report.bill_findings) else None
                self.wrong.append(f"{case_name} bill check {j + 1}: expected {want}, got {have}"
                                  + (f" - \"{f.label}\" ({f.calculation})" if f else ""))
                if have == "needs_confirmation":
                    self.flags.append(f"{case_name} bill check {j + 1}: \"{f.label}\"")
        texts_out = [report.message_to_advisor]
        for lr in report.lines:
            for f in lr.findings:
                texts_out += [f.label, f.next_step or ""]
        for f in report.bill_findings:
            texts_out += [f.label, f.next_step or ""]
        for t in texts_out:
            hits = accusatory_terms(t)
            if hits:
                self.wording.append(f"{case_name}: {hits} in \"{t[:80]}\"")

    def table(self) -> list[str]:
        pct = lambda c: f"{c['correct']} of {c['total']} ({100 * c['correct'] / c['total']:.0f}%)" if c["total"] else "n/a"
        rows = [f"| Line mapped to the expected job | {pct(self.job)} |"]
        rows += [f"| {c.capitalize()} category | {pct(self.per_check[c])} |" for c in CHECKS]
        rows.append(f"| Bill-level arithmetic | {pct(self.bill)} |")
        return rows


def read_photos(paths: list[str], kind: str) -> Document:
    from app.steps.extract import extract_document
    pages = [((ROOT / p).read_bytes(), MIMES[Path(p).suffix.lower()]) for p in paths]
    return extract_document(pages, kind)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", action="store_true", help="use Nemotron for line mapping and the message")
    ap.add_argument("--extract", action="store_true", help="read case photos with Nemotron and audit what it read")
    ap.add_argument("--only", help="only cases whose file name starts with this")
    args = ap.parse_args()
    if (args.models or args.extract) and not settings.models_configured:
        print("NEBIUS_API_KEY is not set; run without --models/--extract or configure .env")
        return 2

    log_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    typed, photo = Track("After correction"), Track("Straight from the photo")
    field_ok = field_tot = 0
    field_misses: list[str] = []
    failures: list[str] = []
    cases = load_cases(args.only)
    real = sum(bool(c.get("real")) for _, c in cases)

    for name, case in cases:
        inp = case["input"]
        print(f"... {name}", flush=True)
        exp = case.get("expected", {})
        texts = [ln["raw_text"] if isinstance(ln["raw_text"], str) else ln["raw_text"].get("value", "")
                 for ln in inp["bill"]["lines"]]
        vehicle = Vehicle.model_validate(inp["vehicle"])
        approvals = [ApprovalNote.model_validate(a) for a in inp.get("approvals", [])]
        bill = Document.model_validate(inp["bill"])
        est = Document.model_validate(inp["estimate"]) if inp.get("estimate") else None

        t0 = time.perf_counter()
        report = run_audit(bill, vehicle, estimate=est, approvals=approvals, use_models=args.models, use_search=False)
        typed.latencies.append(time.perf_counter() - t0)
        typed.score(name, report, exp, texts)

        if args.extract and case.get("images", {}).get("bill"):
            try:
                t0 = time.perf_counter()
                got = read_photos(case["images"]["bill"], "bill")
                got_est = read_photos(case["images"]["estimate"], "estimate") if case["images"].get("estimate") else est
                o, t, m = score_fields(case.get("expected_fields", {}), texts, got)
                field_ok += o
                field_tot += t
                field_misses += [f"{name}: {x}" for x in m]
                rep2 = run_audit(got, vehicle, estimate=got_est, approvals=approvals, use_models=args.models, use_search=False)
                photo.latencies.append(time.perf_counter() - t0)
                photo.score(name, rep2, exp, texts, extracted=got)
            except ModelError as exc:
                failures.append(f"{name}: reading the photo failed ({exc})")

    # model-call breakdown from the call log written during this run
    calls = defaultdict(Counter)
    if settings.call_log.exists():
        for line in settings.call_log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ts", "") < log_start:
                continue
            calls[r["step"]][f"{r['outcome']}@{r['attempt']}"] += 1
    for st in calls.values():
        # The client logs "ok" when the provider answers and "malformed"/"empty" when that answer is unusable.
        st["calls"] = st["ok@1"] + st["timeout@1"] + st["provider_error@1"] + st["empty@1"] - st["empty@1"]
        st["ok_first"] = st["ok@1"] - st["malformed@1"]
        st["failed_first"] = st["malformed@1"] + st["empty@1"]
        st["retry_ok"] = st["ok@2"] - st["malformed@2"]
        st["unusable"] = st["unusable@2"] + st["empty@2"]
        st["busy"] = sum(v for k, v in st.items() if k.startswith("busy_"))
        st["timeout"] = st["timeout@1"] + st["timeout@2"]
        st["provider_error"] = st["provider_error@1"] + st["provider_error@2"]

    gate_ok = not typed.flags and not typed.wording and not photo.wording
    out = ["# FairFix evaluation results", "",
           f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')} · cases: {len(cases)} ({real} real, {len(cases) - real} mock) · "
           f"mode: {'Nemotron mapping and messages' if args.models else 'rules + alias mapping, no network'}"
           + (" + reading photos" if args.extract else ""), "",
           "These are small-sample results for the listed cases only, not evidence of broad real-world reliability.", "",
           f"## Release gate: {'PASS' if gate_ok else 'FAIL'}", "",
           f"- Unsupported flags after correction (Needs confirmation where expected differs): **{len(typed.flags)}**",
           f"- Accusatory wording in labels, next steps or messages: **{len(typed.wording) + len(photo.wording)}**", ""]
    out += [f"  - {x}" for x in typed.flags + typed.wording + photo.wording]
    if failures:
        out += ["", "## Photos that couldn't be read", ""] + [f"- {f}" for f in failures]

    header = ["| Measure | After correction |" + (" Straight from the photo |" if args.extract else ""),
              "| --- | --- |" + (" --- |" if args.extract else "")]
    rows = []
    for a, b in zip(typed.table(), photo.table() if args.extract else typed.table()):
        rows.append(a if not args.extract else a + " " + b.split("|")[2].strip() + " |")
    out += ["", "## Accuracy (raw counts)", ""] + header + rows
    if args.extract:
        pct = f"{field_ok} of {field_tot} ({100 * field_ok / field_tot:.0f}%)" if field_tot else "n/a"
        out += ["", f"**Fields read correctly from the photos** (lines, amounts, quantities, part numbers, totals, "
                    f"GST basis, personal data removed): {pct}"]
        if photo.flags:
            out += ["", "Flags raised straight from the photo that wouldn't be raised after correction "
                        "(the correction screen is there to catch these):", ""] + [f"- {x}" for x in photo.flags]

    out += ["", "## Every wrong verdict after correction (inspect each by hand)", ""]
    out += [f"- {w}" for w in typed.wrong] or ["- None"]
    if args.extract:
        out += ["", "## Every wrong verdict straight from the photo", ""]
        out += [f"- {w}" for w in photo.wrong] or ["- None"]
        out += ["", "## Reading misses", ""]
        out += [f"- {m}" for m in field_misses] or ["- None"]

    out += ["", "## Model calls (from logs/calls.jsonl, this run)", ""]
    if calls:
        out += ["| Step | Calls | OK first try | Failed first try | OK after retry | Still unusable | Busy retries | Timeouts | Provider errors |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for step, st in sorted(calls.items()):
            out.append(f"| {step} | {st['calls']} | {st['ok_first']} | {st['failed_first']} | {st['retry_ok']} | "
                       f"{st['unusable']} | {st['busy']} | {st['timeout']} | {st['provider_error']} |")
    else:
        out.append("No model calls in this run.")
    lat = photo.latencies or typed.latencies
    if lat:
        out += ["", f"Time per audit{' (reading the photo included)' if photo.latencies else ''}: "
                    f"median {statistics.median(lat):.1f} s, slowest {max(lat):.1f} s. Price search was off."]
    (ROOT / "eval" / "results.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
