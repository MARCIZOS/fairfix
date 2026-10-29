"""Accounts and sessions: SQLite locally, Postgres when DATABASE_URL is set (e.g. Neon on a free host,
whose own disk is wiped on every restart).

Stores only what sign-in needs: email, a scrypt password hash, timestamps, and
server-side sessions (kept as SHA-256 hashes of the cookie token, so a copy of the
database can't be used to sign in). Bills and results are never linked to an account.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from .config import settings

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
# scrypt parameters (OWASP baseline): N=2^15, r=8, p=1 -> ~32 MB, ~50 ms per hash
N, R, P, DKLEN = 2 ** 15, 8, 1, 32
COMMON = {"password", "password1", "12345678", "123456789", "1234567890", "qwerty123", "11111111",
          "iloveyou", "admin123", "welcome1", "abc12345", "password123", "india123", "qwertyuiop"}


class AccountError(ValueError):
    """A problem the user can fix (shown to them as is)."""


@dataclass(frozen=True)
class User:
    id: int
    email: str


SCHEMA = {
    "sqlite": """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL, last_login INTEGER);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);""",
    "postgres": """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL,
            created_at BIGINT NOT NULL, last_login BIGINT);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at BIGINT NOT NULL);
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);""",
}
_ready: set[str] = set()
_ready_lock = threading.Lock()


class _Tx:
    """One transaction. SQL is written with ? placeholders and adapted for Postgres."""

    def __init__(self, con, pg: bool):
        self.con, self.pg = con, pg

    def execute(self, sql: str, params: tuple = ()):
        return self.con.execute(sql.replace("?", "%s") if self.pg else sql, params)


@contextmanager
def _tx():
    pg = bool(settings.database_url)
    if pg:
        import psycopg                                  # only needed when DATABASE_URL is set
        con = psycopg.connect(settings.database_url, connect_timeout=10)
    else:
        settings.users_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(settings.users_db, timeout=10)
        con.execute("PRAGMA foreign_keys = ON")
    key = settings.database_url or str(settings.users_db)
    try:
        if key not in _ready:
            with _ready_lock:
                for stmt in filter(str.strip, SCHEMA["postgres" if pg else "sqlite"].split(";")):
                    con.execute(stmt)
                con.commit()
                _ready.add(key)
        yield _Tx(con, pg)
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


# ------------------------------------------------------------------ passwords


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN, maxmem=64 * 1024 * 1024)
    return f"scrypt${N}${R}${P}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(dk) // 2, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


_DUMMY = None


def _dummy_hash() -> str:
    """A real hash to compare against when the email is unknown, so timing doesn't reveal accounts."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_hex(8))
    return _DUMMY


def normalise_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL.match(e) or len(e) > 254:
        raise AccountError("Enter a valid email address.")
    return e


def check_password_rules(pw: str, email: str) -> None:
    if len(pw) < 8:
        raise AccountError("Use at least 8 characters for the password.")
    if len(pw) > 128:
        raise AccountError("Use at most 128 characters for the password.")
    low = pw.lower()
    if low in COMMON or low == email or low == email.split("@")[0] or len(set(pw)) < 4:
        raise AccountError("That password is too easy to guess. Try a longer phrase.")


# ------------------------------------------------------------------ accounts


def create_user(email: str, pw: str) -> User:
    email = normalise_email(email)
    check_password_rules(pw, email)
    with _tx() as con:
        if con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AccountError("An account with this email already exists. Sign in instead.")
        row = con.execute("INSERT INTO users (email, pw_hash, created_at) VALUES (?, ?, ?) RETURNING id",
                          (email, hash_password(pw), int(time.time()))).fetchone()
        return User(row[0], email)


def authenticate(email: str, pw: str) -> User | None:
    try:
        email = normalise_email(email)
    except AccountError:
        verify_password(pw, _dummy_hash())
        return None
    with _tx() as con:
        row = con.execute("SELECT id, pw_hash FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            verify_password(pw, _dummy_hash())
            return None
        if not verify_password(pw, row[1]):
            return None
        con.execute("UPDATE users SET last_login = ? WHERE id = ?", (int(time.time()), row[0]))
        return User(row[0], email)


def delete_user(user_id: int) -> None:
    with _tx() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def password_matches(user_id: int, pw: str) -> bool:
    with _tx() as con:
        row = con.execute("SELECT pw_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row) and verify_password(pw, row[0])


# ------------------------------------------------------------------ sessions


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id: int) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    ttl = int(settings.session_days * 86400)
    now = int(time.time())
    with _tx() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        con.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (_h(token), user_id, now + ttl))
    return token, ttl


def session_user(token: str | None) -> User | None:
    if not token or len(token) > 100:
        return None
    with _tx() as con:
        row = con.execute("SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token_hash = ? AND s.expires_at > ?", (_h(token), int(time.time()))).fetchone()
    return User(row[0], row[1]) if row else None


def end_session(token: str | None) -> None:
    if token:
        with _tx() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_h(token),))
