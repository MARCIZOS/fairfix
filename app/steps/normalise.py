"""Step 4 - Normalise: map each bill line to one standard job.

1. A deterministic alias match handles common bill wording.
2. Remaining lines go to the text model in one call, with the fixed job list.
3. Anything below the confidence threshold, or any job id not in the list,
   becomes "unrecognised". Lines are never dropped and the original bill
   text is always kept beside the job.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..config import settings
from ..jobs import JOBS, UNRECOGNISED, alias_match, get_job
from ..llm import ModelError, call_json, untrusted
from ..schemas import Document


class _Map(BaseModel):
    index: int
    job: str
    confidence: float = 0.0


class _Out(BaseModel):
    mappings: list[_Map] = []


def _job_list() -> str:
    return "\n".join(f"- {j.id}: {j.label}" for j in JOBS)


def normalise(doc: Document, *, use_model: bool = True) -> list[str]:
    """Fill line.job / job_confidence / job_source in place. Returns warnings."""
    warnings: list[str] = []
    pending: list[int] = []
    for i, ln in enumerate(doc.lines):
        if ln.job_source == "user" and ln.job:
            continue  # the user picked the job on the correction screen
        jid = alias_match(ln.raw_text.value or "")
        if jid:
            ln.job, ln.job_confidence, ln.job_source = jid, 1.0, "alias"
        else:
            pending.append(i)

    if pending and use_model and settings.models_configured:
        listing = "\n".join(f"{i}: {doc.lines[i].raw_text.value or ''}" for i in pending)
        msgs = [
            {"role": "system", "content":
                "Map each Indian vehicle service bill line to ONE job id from this list, or to "
                f"\"{UNRECOGNISED}\" if you are not confident. Never invent a job id.\n{_job_list()}\n"
                "Return JSON: {\"mappings\": [{\"index\": n, \"job\": \"job_id\", \"confidence\": 0.0-1.0}]}. "
                "Use confidence below 0.7 when the text is a shop code you can't read with certainty."},
            {"role": "user", "content": untrusted("BILL LINES", listing)},
        ]
        try:
            out = call_json("normalise", settings.model_text, msgs, _Out)
            got = {m.index: m for m in out.mappings}
        except ModelError as exc:
            warnings.append(f"Line mapping by model unavailable ({exc.kind}); unmatched lines are marked unrecognised.")
            got = {}
    else:
        got = {}
        if pending and use_model and not settings.models_configured:
            warnings.append("Model not configured; lines without a known wording are marked unrecognised.")

    for i in pending:
        ln = doc.lines[i]
        m = got.get(i)
        if m and get_job(m.job) and m.confidence >= settings.normalise_min_confidence:
            ln.job, ln.job_confidence, ln.job_source = m.job, round(m.confidence, 2), "model"
        else:
            ln.job, ln.job_confidence, ln.job_source = UNRECOGNISED, (round(m.confidence, 2) if m else None), "model" if m else "none"
        job = get_job(ln.job or "")
        if job and job.kind == "labour" and ln.kind == "unknown":
            ln.kind = "labour"
    for ln in doc.lines:
        job = get_job(ln.job or "")
        if job and job.kind == "labour" and ln.kind == "unknown":
            ln.kind = "labour"
    return warnings
