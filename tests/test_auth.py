"""Email + password accounts, sessions and rate limits."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, users
from app import config as cfg
from app import main as main_mod

ROOT = Path(__file__).resolve().parent.parent
PW = "carbon-copy-2026"


PG = os.environ.get("TEST_DATABASE_URL", "")   # set to also run these tests against Postgres


@pytest.fixture
def on(monkeypatch, tmp_path):
    s = replace(cfg.settings, auth_required=True, users_db=tmp_path / "users.sqlite", database_url=PG,
                rate_audit_per_hour=3, rate_read_per_hour=2, rate_ip_per_hour=100, login_attempts=3, signups_per_hour=4)
    for mod in (auth, users, main_mod):
        monkeypatch.setattr(mod, "settings", s)
    if PG:
        with users._tx() as con:
            con.execute("DELETE FROM sessions")
            con.execute("DELETE FROM users")
    auth.limiter.reset()
    yield TestClient(main_mod.app)
    auth.limiter.reset()


def audit_body():
    inp = json.loads((ROOT / "eval" / "cases" / "sample_scooter_service.json").read_text())["input"]
    return {"bill": inp["bill"], "vehicle": inp["vehicle"], "approvals": inp.get("approvals", [])}


def signup(c, email="asha@example.com", pw=PW, **kw):
    return c.post("/api/signup", json={"email": email, "password": pw, **kw})


def test_open_when_auth_off():
    c = TestClient(main_mod.app)
    assert c.get("/api/session").json()["signed_in"] is True


def test_samples_and_dashboard_stay_open(on):
    assert on.get("/api/demo/sample_scooter_service").status_code == 200
    assert on.get("/api/eval").status_code == 200
    assert on.get("/api/session").json()["signed_in"] is False


def test_audit_needs_sign_in(on):
    assert on.post("/api/audit", json=audit_body()).status_code == 401


def test_signup_signs_in_and_unlocks(on):
    r = signup(on, email="  Asha@Example.com ")
    assert r.status_code == 200 and r.json()["email"] == "asha@example.com"
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc
    assert on.post("/api/audit", json=audit_body()).status_code == 200


def test_password_stored_as_scrypt_hash(on):
    signup(on)
    with users._tx() as con:
        stored = con.execute("SELECT pw_hash FROM users").fetchone()[0]
        token_hashes = [r[0] for r in con.execute("SELECT token_hash FROM sessions").fetchall()]
    assert stored.startswith("scrypt$") and PW not in stored
    assert len(token_hashes) == 1 and len(token_hashes[0]) == 64


@pytest.mark.parametrize("email,pw,msg", [
    ("not-an-email", PW, "valid email"), ("a@b.co", "short", "at least 8"),
    ("a@b.co", "password123", "too easy"), ("a@b.co", "aaaaaaaaaa", "too easy")])
def test_signup_validation(on, email, pw, msg):
    r = signup(on, email=email, pw=pw)
    assert r.status_code == 400 and msg in r.json()["detail"]


def test_duplicate_email_refused(on):
    signup(on)
    on.post("/api/logout")
    r = signup(on, email="ASHA@example.com")
    assert r.status_code == 400 and "already exists" in r.json()["detail"]


def test_login_logout(on):
    signup(on)
    on.post("/api/logout")
    assert on.get("/api/session").json()["signed_in"] is False
    assert on.post("/api/audit", json=audit_body()).status_code == 401
    r = on.post("/api/login", json={"email": "asha@example.com", "password": PW})
    assert r.status_code == 200 and on.get("/api/session").json()["email"] == "asha@example.com"


def test_logout_kills_session_server_side(on):
    signup(on)
    token = on.cookies.get(auth.COOKIE)
    on.post("/api/logout")
    other = TestClient(main_mod.app, cookies={auth.COOKIE: token})   # a copied cookie no longer works
    assert other.get("/api/session").json()["signed_in"] is False


def test_wrong_password_generic_then_lockout(on):
    signup(on)
    on.post("/api/logout")
    for _ in range(3):
        r = on.post("/api/login", json={"email": "asha@example.com", "password": "wrong-password-1"})
        assert r.status_code == 401 and "Email or password is incorrect" in r.json()["detail"]
    r = on.post("/api/login", json={"email": "asha@example.com", "password": PW})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_unknown_email_same_message(on):
    r = on.post("/api/login", json={"email": "nobody@example.com", "password": PW})
    assert r.status_code == 401 and r.json()["detail"].startswith("Email or password is incorrect")


def test_rate_limit_per_account(on):
    signup(on)
    codes = [on.post("/api/audit", json=audit_body()).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_signup_limit_per_ip(on):
    codes = [signup(on, email=f"u{i}@example.com").status_code for i in range(5)]
    assert codes[-1] == 429


def test_invite_code_when_configured(on, monkeypatch):
    s = replace(main_mod.settings, signup_codes=("judge-2026",))
    for mod in (auth, users, main_mod):
        monkeypatch.setattr(mod, "settings", s)
    assert on.get("/api/session").json()["invite_required"] is True
    assert signup(on).status_code == 403
    assert signup(on, invite_code="judge-2026").status_code == 200


def test_delete_account(on):
    signup(on)
    assert on.post("/api/account/delete", json={"password": "wrong-password-1"}).status_code == 403
    assert on.post("/api/account/delete", json={"password": PW}).status_code == 200
    assert on.get("/api/session").json()["signed_in"] is False
    r = on.post("/api/login", json={"email": "asha@example.com", "password": PW})
    assert r.status_code == 401


def test_cross_site_post_refused(on):
    signup(on)
    r = on.post("/api/audit", json=audit_body(), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_message_without_session_uses_template(on):
    rep = on.get("/api/demo/sample_scooter_service").json()["report"]
    r = on.post("/api/message", json={"report": rep, "lang": "hi"})
    assert r.status_code == 200 and r.json()["source"] == "template"


def test_security_headers(on):
    h = on.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["x-content-type-options"] == "nosniff"


def test_signin_page_served(on):
    r = on.get("/signin.html")
    assert r.status_code == 200 and "signin.js" in r.text
