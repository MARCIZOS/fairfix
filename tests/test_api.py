"""API and pipeline tests. Model calls are stubbed, so these run offline."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as cfg
from app import audit as audit_mod
from app import llm
from app.main import app
from app.schemas import Document, Vehicle
from app.steps import extract, message, normalise

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)


def case(name):
    return json.loads((ROOT / "eval" / "cases" / f"{name}.json").read_text())


def test_health_without_keys():
    r = client.get("/health")
    assert r.status_code == 200 and "models_configured" in r.json()


def test_vehicles_lists_sample_and_templates():
    v = {x["vehicle_id"]: x for x in client.get("/api/vehicles").json()}
    assert v["fairfix_sample_scooter"]["supported"] and v["fairfix_sample_scooter"]["fictional"]
    assert v["honda_activa"]["supported"] is False


def test_demo_runs_real_rules():
    r = client.get("/api/demo/sample_scooter_service")
    assert r.status_code == 200
    rep = r.json()["report"]
    labels = [f["label"] for ln in rep["lines"] for f in ln["findings"]]
    assert "Matches schedule (due by time)" in labels
    assert "Not present in the uploaded estimate: confirm authorization" in labels
    assert rep["message_source"] == "template"
    assert any("fictional" in w.lower() for w in rep["warnings"])
    assert rep["needs_confirmation_total_inr"] == 450


def test_demo_rejects_non_demo_and_traversal():
    assert client.get("/api/demo/mock_unsupported_math").status_code == 404
    assert client.get("/api/demo/..%2F..%2Fapp%2Fmain").status_code == 404


def test_audit_endpoint_partial_for_unsupported_vehicle():
    inp = case("mock_unsupported_math")["input"]
    r = client.post("/api/audit", json={"bill": inp["bill"], "vehicle": inp["vehicle"]})
    assert r.status_code == 200
    rep = r.json()
    assert rep["partial_audit"] is True and rep["partial_reason"]


def test_extract_needs_key():
    r = client.post("/api/extract", files={"bill": ("b.jpg", b"\xff\xd8\xff", "image/jpeg")})
    assert r.status_code == 503


def test_hindi_template_message():
    r = client.get("/api/demo/sample_scooter_service?lang=hi")
    msg = r.json()["report"]["message_to_advisor"]
    assert "नमस्ते" in msg and "धन्यवाद" in msg


# ------------------------------------------------------------ stubbed model paths


@pytest.fixture
def keyed(monkeypatch):
    s = replace(cfg.settings, nebius_api_key="test-key")
    for mod in (cfg, llm, extract, normalise, message, audit_mod):
        monkeypatch.setattr(mod, "settings", s, raising=False)
    return s


def test_extract_maps_and_redacts(keyed, monkeypatch):
    raw = {"doc_date": "2026-10-20", "lines": [
        {"text": "AIR CLNR ELEMENT UP14AB1234", "qty": "1", "unit_price": "₹320.00", "amount": "320"},
        {"text": "Call 9876543210 for pickup", "amount": 0}],
        "grand_total": "₹ 2,595.00", "notes": ["customer Ravi 9876543210"]}

    def fake_json(step, model, messages, schema, **kw):
        return schema.model_validate(raw)

    monkeypatch.setattr(extract, "call_json", fake_json)
    doc = extract.extract_document([(b"x", "image/jpeg")])
    assert doc.lines[0].unit_price_inr.value == 320.0
    assert doc.grand_total_inr.value == 2595.0
    assert "UP14AB1234" not in doc.lines[0].raw_text.value
    assert "9876543210" not in doc.lines[1].raw_text.value
    assert "9876543210" not in " ".join(doc.extraction_notes)


def test_normalise_low_confidence_becomes_unrecognised(keyed, monkeypatch):
    doc = Document.model_validate({"lines": [{"raw_text": "XQ-7 SPL"}, {"raw_text": "ZZ PROTECT"}, {"raw_text": "air filter"}]})

    def fake_json(step, model, messages, schema, **kw):
        return schema.model_validate({"mappings": [
            {"index": 0, "job": "engine_flush", "confidence": 0.4},
            {"index": 1, "job": "made_up_job", "confidence": 0.99}]})

    monkeypatch.setattr(normalise, "call_json", fake_json)
    normalise.normalise(doc)
    assert [l.job for l in doc.lines] == ["unrecognised", "unrecognised", "air_filter_replacement"]
    assert doc.lines[0].raw_text.value == "XQ-7 SPL"  # original text always kept


def test_normalise_model_failure_marks_unrecognised(keyed, monkeypatch):
    doc = Document.model_validate({"lines": [{"raw_text": "XQ-7 SPL"}]})

    def boom(*a, **k):
        raise llm.ModelError("timeout", "normalise")

    monkeypatch.setattr(normalise, "call_json", boom)
    w = normalise.normalise(doc)
    assert doc.lines[0].job == "unrecognised" and w


def test_accusatory_model_message_falls_back_to_template(keyed, monkeypatch):
    inp = case("sample_scooter_service")["input"]
    monkeypatch.setattr(message, "call_text", lambda *a, **k: "You were clearly overcharged for the flush.")
    rep = audit_mod.run_audit(Document.model_validate(inp["bill"]), Vehicle.model_validate(inp["vehicle"]),
                              estimate=Document.model_validate(inp["estimate"]), use_search=False, use_models=True)
    assert rep.message_source == "template"
    assert "overcharged" not in rep.message_to_advisor


def test_neutral_model_message_is_used(keyed, monkeypatch):
    inp = case("sample_scooter_service")["input"]
    monkeypatch.setattr(message, "call_text", lambda *a, **k: "Hi! Could you tell me why the engine flush was recommended? Thanks.")
    rep = audit_mod.run_audit(Document.model_validate(inp["bill"]), Vehicle.model_validate(inp["vehicle"]),
                              estimate=Document.model_validate(inp["estimate"]), use_search=False, use_models=True)
    assert rep.message_source == "model"


def test_price_search_failure_does_not_block_audit(keyed, monkeypatch):
    inp = case("sample_scooter_service")["input"]

    def boom(*a, **k):
        raise RuntimeError("search down")

    monkeypatch.setattr(audit_mod.prices, "lookup_comparables", boom)
    monkeypatch.setattr(message, "call_text", lambda *a, **k: "Thanks")
    rep = audit_mod.run_audit(Document.model_validate(inp["bill"]), Vehicle.model_validate(inp["vehicle"]),
                              estimate=Document.model_validate(inp["estimate"]), use_search=True, use_models=True)
    assert len(rep.lines) == 6
    assert any("Price lookup failed" in w for w in rep.warnings)
    assert all(f.category == "unable_to_verify" for lr in rep.lines for f in lr.findings if f.check == "price")


def test_price_extraction_drops_excerpts_not_on_page(keyed, monkeypatch):
    from app.steps import prices
    from tests.helpers import line, vehicle

    page = "Genuine air filter for Sample Scooter. Price: ₹ 340.00 incl. GST. In stock. " * 5

    def fake_json(step, model, messages, schema, **kw):
        return schema.model_validate({"listings": [
            {"part_name": "Air filter", "grade": "genuine", "price_inr": 340, "gst_included": True,
             "listing_type": "part_only", "excerpt": "Price: ₹ 340.00 incl. GST."},
            {"part_name": "Air filter", "grade": "genuine", "price_inr": 99, "gst_included": True,
             "listing_type": "part_only", "excerpt": "Price: ₹ 99 special"},          # not on the page
            {"part_name": "Air filter", "grade": "genuine", "price_inr": 299, "gst_included": True,
             "listing_type": "part_only", "excerpt": "Price: ₹ 340.00 incl. GST."}]})  # price not in excerpt

    monkeypatch.setattr(prices, "call_json", fake_json)
    facts = prices.extract_listings(page, "https://www.boodmo.com/x", line("air filter", "air_filter_replacement"), vehicle())
    assert len(facts) == 1 and facts[0].data["price_inr"] == 340


# ------------------------------------------------------------ reasoning-model behaviour


class _Msg:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content, finish="stop"): self.message = _Msg(content); self.finish_reason = finish


class _Resp:
    def __init__(self, content, finish="stop"): self.choices = [_Choice(content, finish)]; self.usage = None


class _FakeClient:
    """Records calls; returns queued responses or raises queued exceptions."""

    def __init__(self, queue):
        self.queue, self.calls = list(queue), []
        self.chat = type("C", (), {"completions": type("K", (), {"create": self._create})()})()

    def _create(self, **kw):
        self.calls.append(kw)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _BadRequest(Exception):
    status_code = 400


def _fake(monkeypatch, keyed, queue):
    fc = _FakeClient(queue)
    monkeypatch.setattr(llm, "client", lambda: fc)
    monkeypatch.setattr(llm, "log_call", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_thinking_kwargs_ok", True)
    return fc


def test_thinking_disabled_by_default(keyed, monkeypatch):
    fc = _fake(monkeypatch, keyed, [_Resp("FAIRFIX OK")])
    assert llm.call_text("t", "m", [{"role": "user", "content": "x"}]) == "FAIRFIX OK"
    assert fc.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_rejected_thinking_flag_is_dropped(keyed, monkeypatch):
    fc = _fake(monkeypatch, keyed, [_BadRequest("bad"), _Resp("FAIRFIX OK")])
    assert llm.call_text("t", "m", [{"role": "user", "content": "x"}]) == "FAIRFIX OK"
    assert "extra_body" not in fc.calls[1] and llm._thinking_kwargs_ok is False


def test_empty_answer_is_an_error_not_a_pass(keyed, monkeypatch):
    _fake(monkeypatch, keyed, [_Resp("", "length")])
    with pytest.raises(llm.ModelError) as e:
        llm.call_text("t", "m", [{"role": "user", "content": "x"}])
    assert e.value.kind == "empty_output"


def test_only_thinking_counts_as_empty(keyed, monkeypatch):
    _fake(monkeypatch, keyed, [_Resp("<think>hmm</think>  ")])
    with pytest.raises(llm.ModelError):
        llm.call_text("t", "m", [{"role": "user", "content": "x"}])


def test_json_retries_empty_with_bigger_budget(keyed, monkeypatch):
    from pydantic import BaseModel

    class Out(BaseModel):
        a: int

    fc = _fake(monkeypatch, keyed, [_Resp("", "length"), _Resp('{"a": 1}')])
    assert llm.call_json("t", "m", [{"role": "user", "content": "x"}], Out, max_tokens=100).a == 1
    assert fc.calls[1]["max_tokens"] == 200


class _Busy(Exception):
    status_code = 529


def test_busy_provider_is_retried(keyed, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    fc = _fake(monkeypatch, keyed, [_Busy("overloaded"), _Busy("overloaded"), _Resp("FAIRFIX OK")])
    assert llm.call_text("t", "m", [{"role": "user", "content": "x"}]) == "FAIRFIX OK"
    assert len(fc.calls) == 3


def test_busy_provider_gives_up_after_retries(keyed, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    _fake(monkeypatch, keyed, [_Busy("x"), _Busy("x"), _Busy("x")])
    with pytest.raises(llm.ModelError) as e:
        llm.call_text("t", "m", [{"role": "user", "content": "x"}])
    assert e.value.kind == "provider_error" and "529" in str(e.value)


def test_reading_hints_catch_tax_line_taken_as_total_and_blank_rows():
    from app.main import reading_hints
    d = Document.model_validate({"lines": [{"raw_text": "ENG OIL", "line_total_inr": 348}, {"raw_text": "AIR", "line_total_inr": None}],
                                 "tax_lines": [{"name": "SGST", "amount_inr": 93.81}], "grand_total_inr": 93.81})
    h = reading_hints(d)
    assert any("same as a tax line" in x for x in h)
    assert any("smaller than one of the line amounts" in x for x in h)
    assert any("line 2" in x for x in h)
    clean = Document.model_validate({"lines": [{"raw_text": "a", "line_total_inr": 100}], "grand_total_inr": 118})
    assert reading_hints(clean) == []


def test_audit_report_has_a_real_trace():
    rep = client.get("/api/demo/sample_scooter_service").json()["report"]
    steps = [t["step"] for t in rep["trace"]]
    assert steps == ["map", "facts", "prices", "decide", "message"]
    assert all(t["ms"] >= 0 for t in rep["trace"])
    assert next(t for t in rep["trace"] if t["step"] == "decide")["engine"] == "Python rules"


def test_display_model_names():
    from app.audit import display_model
    assert display_model("nvidia/Nemotron-3.5-Super-VL") == "Nemotron 3.5 Super VL"
    assert display_model("nvidia/nemotron-3-super-120b-a12b") == "Nemotron 3 Super"
    assert display_model("nvidia/Nemotron-3-Ultra-550b-a55b") == "Nemotron 3 Ultra"


def test_eval_endpoint_parses_results():
    e = client.get("/api/eval").json()
    cur = e["current"]
    assert cur["gate"] in ("PASS", "FAIL") and cur["cases"] >= 1
    assert cur["accuracy"] and all(r["after_correction"]["total"] > 0 for r in cur["accuracy"])


def test_stats_endpoint_shape(tmp_path, monkeypatch):
    from app import evalreport
    log = tmp_path / "calls.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"ts": "2026-10-28T10:00:00+00:00", "step": "extract", "model": "m1", "outcome": "ok", "attempt": 1, "ms": 2000, "prompt_tokens": 10, "completion_tokens": 5},
        {"ts": "2026-10-28T10:00:01+00:00", "step": "extract", "model": "m1", "outcome": "busy_529", "attempt": 1, "ms": 300},
        {"ts": "2026-10-28T10:00:02+00:00", "step": "message", "model": "m2", "outcome": "ok", "attempt": 1, "ms": 1000}]))
    monkeypatch.setattr(evalreport, "settings", replace(cfg.settings, call_log=log))
    monkeypatch.setattr(evalreport, "SNAPSHOT", tmp_path / "none.jsonl")
    s = evalreport.call_stats()
    assert s["total_calls"] == 2 and s["outcomes"]["busy_retry"] == 1
    assert s["by_model"][0]["prompt_tokens"] == 10
