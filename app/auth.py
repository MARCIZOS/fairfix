"""Sign-in, sessions and rate limits for the HTTP layer.

Accounts live in app/users.py. The session cookie holds a random token only; the server
keeps its hash and the expiry, so signing out ends the session everywhere it was copied.
Endpoints that spend model credits need a signed-in user and are rate limited per
account and per IP. The samples and the dashboard stay open.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, Response

from . import users
from .config import settings

COOKIE = "ff_session"


def enabled() -> bool:
    return settings.auth_required


# ------------------------------------------------------------------ rate limits


class Limiter:
    """Sliding-window counters kept in memory (one process; fine for a single instance)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> float:
        """Record a hit. Returns 0 if allowed, else seconds until the next slot frees up."""
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return max(1.0, q[0] + window_s - now)
            q.append(now)
            return 0.0

    def count(self, key: str, window_s: float) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for t in self._hits.get(key, ()) if t > now - window_s)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = Limiter()


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _too_many(wait: float, what: str) -> HTTPException:
    mins = max(1, round(wait / 60))
    return HTTPException(429, f"Too many {what}. Try again in about {mins} minute{'s' if mins != 1 else ''}.",
                         headers={"Retry-After": str(int(wait))})


def same_origin(request: Request) -> None:
    """Refuse cross-site requests that carry the cookie (defence in depth on top of SameSite=Lax)."""
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host", ""):
        raise HTTPException(403, "Cross-site request refused.")


def current_user(request: Request) -> users.User | None:
    return users.session_user(request.cookies.get(COOKIE))


# ------------------------------------------------------------------ dependencies


def require_user(kind: str):
    """FastAPI dependency: a signed-in user, within the rate limits, for model-backed endpoints."""
    def dep(request: Request) -> users.User | None:
        if not enabled():
            return None
        same_origin(request)
        user = current_user(request)
        if not user:
            raise HTTPException(401, "Sign in to check your own bill.")
        wait = limiter.hit(f"ip:{client_ip(request)}", settings.rate_ip_per_hour, 3600)
        if wait:
            raise _too_many(wait, "requests from this network")
        per_user = settings.rate_read_per_hour if kind == "read" else settings.rate_audit_per_hour
        wait = limiter.hit(f"{kind}:{user.id}", per_user, 3600)
        if wait:
            raise _too_many(wait, "bill readings" if kind == "read" else "checks")
        return user
    return dep


def _set_cookie(request: Request, response: Response, user: users.User) -> None:
    token, ttl = users.new_session(user.id)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, max_age=ttl, httponly=True, samesite="lax", secure=secure, path="/")


def signup(request: Request, response: Response, email: str, password: str, code: str | None) -> users.User:
    same_origin(request)
    if settings.signup_codes and not any(hmac.compare_digest((code or "").strip(), c) for c in settings.signup_codes):
        raise HTTPException(403, "That invite code isn't right.")
    wait = limiter.hit(f"signup:{client_ip(request)}", settings.signups_per_hour, 3600)
    if wait:
        raise _too_many(wait, "new accounts from this network")
    try:
        user = users.create_user(email, password)
    except users.AccountError as exc:
        raise HTTPException(400, str(exc))
    _set_cookie(request, response, user)
    return user


def login(request: Request, response: Response, email: str, password: str) -> users.User:
    same_origin(request)
    key = f"login:{client_ip(request)}"
    if limiter.count(key, 900) >= settings.login_attempts:
        raise _too_many(900, "failed sign-ins")
    user = users.authenticate(email, password)
    if not user:
        limiter.hit(key, 10**6, 900)
        left = settings.login_attempts - limiter.count(key, 900)
        raise HTTPException(401, "Email or password is incorrect." + (f" {left} tries left." if left <= 2 else ""))
    _set_cookie(request, response, user)
    return user


def logout(request: Request, response: Response) -> None:
    users.end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")


def delete_account(request: Request, response: Response, password: str) -> None:
    same_origin(request)
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in first.")
    if not users.password_matches(user.id, password):
        raise HTTPException(403, "That password isn't right.")
    users.delete_user(user.id)          # sessions go with it (ON DELETE CASCADE)
    response.delete_cookie(COOKIE, path="/")
