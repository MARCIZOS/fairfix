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
    inp = case("sample_scooter_service")["input"]
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
