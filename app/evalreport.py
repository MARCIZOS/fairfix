"""Read evaluation results and the model-call log for the dashboard.

- parse_results(): turns eval/results.md (written by eval/run_eval.py) into JSON.
- call_stats(): aggregates logs/calls.jsonl (model, step, timing, outcome only;
  the log never holds bill content).
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

from .config import ROOT, settings

FRACTION = re.compile(r"(\d+) of (\d+)")
SNAPSHOT = ROOT / "eval" / "model_calls.jsonl"   # call log captured with the evaluation (timings only)


def _frac(cell: str) -> dict | None:
    m = FRACTION.search(cell or "")
    if not m:
        return None
    ok, tot = int(m.group(1)), int(m.group(2))
    return {"ok": ok, "total": tot, "pct": round(100 * ok / tot, 1) if tot else None}


def _section(text: str, heading: str) -> str:
    """Body of '## heading' up to the next '## '."""
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def _bullets(body: str) -> list[str]:
    return [ln[2:].strip() for ln in body.splitlines() if ln.startswith("- ") and ln[2:].strip() != "None"]


def parse_results(path: Path) -> dict | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    out: dict = {"file": path.name}

    m = re.search(r"^Run: (.+?) · cases: (\d+) \((\d+) real, (\d+) mock\) · mode: (.+)$", text, re.M)
    if m:
        out.update(run=m.group(1), cases=int(m.group(2)), real=int(m.group(3)), mock=int(m.group(4)), mode=m.group(5).strip())
    m = re.search(r"^## Release gate: (PASS|FAIL)", text, re.M)
    out["gate"] = m.group(1) if m else None
    m = re.search(r"Unsupported flags[^*]*\*\*(\d+)\*\*", text)
    out["unsupported_flags"] = int(m.group(1)) if m else None
    m = re.search(r"Accusatory wording[^*]*\*\*(\d+)\*\*", text)
    out["accusatory"] = int(m.group(1)) if m else None

    # accuracy table: | Measure | After correction | Straight from the photo |
    acc = []
    for ln in _section(text, "Accuracy").splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 2 and _frac(cells[1]):
            acc.append({"measure": cells[0], "after_correction": _frac(cells[1]),
                        "from_photo": _frac(cells[2]) if len(cells) > 2 else None})
    out["accuracy"] = acc
    m = re.search(r"\*\*Fields read correctly from the photos\*\*[^:]*: (\d+ of \d+)", text)
    out["fields_read"] = _frac(m.group(1)) if m else None

    m = re.search(r"Flags raised straight from the photo[^\n]*\n\n(.*?)(?=\n## |\Z)", text, re.S)
    out["photo_flags"] = _bullets(m.group(1)) if m else []
    out["wrong_after_correction"] = _bullets(_section(text, "Every wrong verdict after correction"))
    out["wrong_from_photo"] = _bullets(_section(text, "Every wrong verdict straight from the photo"))
    out["reading_misses"] = _bullets(_section(text, "Reading misses"))
    out["personal_data_kept"] = sum("PERSONAL DATA KEPT" in m for m in out["reading_misses"])

    calls = []
    for ln in _section(text, "Model calls").splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 6 and cells[1].isdigit():
            names = ["calls", "ok_first", "failed_first", "retry_ok", "unusable", "busy", "timeouts", "provider_errors"]
            row = {"step": cells[0]}
            row.update({n: int(v) for n, v in zip(names, cells[1:]) if v.isdigit()})
            calls.append(row)
    out["model_calls"] = calls
    m = re.search(r"median ([\d.]+) s, slowest ([\d.]+) s", text)
    out["latency_s"] = {"median": float(m.group(1)), "slowest": float(m.group(2))} if m else None
    return out


def eval_summary() -> dict:
    d = ROOT / "eval"
    return {"current": parse_results(d / "results.md"), "before_fixes": parse_results(d / "results_before_fixes.md")}


def call_stats(limit: int = 5000) -> dict:
    """Per-model and per-step counts, latency and tokens from the call log (no content is logged)."""
    # The live log, plus the snapshot committed with the evaluation (so a fresh deploy, whose
    # disk starts empty, still shows the measured timings). Duplicates are counted once.
    rows, seen = [], set()
    for path in (SNAPSHOT, settings.call_log):
        if not path.exists():
            continue
        for ln in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            key = (r.get("ts"), r.get("step"), r.get("model"), r.get("ms"), r.get("outcome"))
            if key not in seen:
                seen.add(key)
                rows.append(r)
    rows.sort(key=lambda r: r.get("ts") or "")
    if not rows:
        return {"total_calls": 0, "by_model": [], "by_step": [], "outcomes": {}, "since": None}
    answered = [r for r in rows if r.get("outcome") == "ok"]
    outcomes: dict[str, int] = defaultdict(int)
    for r in rows:
        k = r.get("outcome", "?")
        outcomes["busy_retry" if k.startswith("busy_") else k] += 1

    def group(key: str) -> list[dict]:
        g: dict[str, list[dict]] = defaultdict(list)
        for r in answered:
            g[r.get(key) or "?"].append(r)
        res = []
        for name, rs in g.items():
            ms = [r["ms"] for r in rs if r.get("ms")]
            res.append({key: name, "calls": len(rs),
                        "median_ms": round(statistics.median(ms)) if ms else None,
                        "p90_ms": round(sorted(ms)[int(0.9 * (len(ms) - 1))]) if ms else None,
                        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in rs),
                        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in rs)})
        return sorted(res, key=lambda x: -x["calls"])

    return {"total_calls": len(answered), "by_model": group("model"), "by_step": group("step"),
            "outcomes": dict(outcomes), "since": rows[0].get("ts") if rows else None,
            "last_error": next((r.get("ts") for r in reversed(rows) if r.get("outcome") not in ("ok", None)), None),
            "until": rows[-1].get("ts") if rows else None}
