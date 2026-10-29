"""Configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional: load .env when python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Nebius Token Factory (OpenAI-compatible)
    nebius_base_url: str = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
    nebius_api_key: str = os.environ.get("NEBIUS_API_KEY", "")
    model_vision: str = os.environ.get("MODEL_VISION", "nvidia/Nemotron-3.5-Super-VL")
    model_text: str = os.environ.get("MODEL_TEXT", "nvidia/nemotron-3-super-120b-a12b")
    model_explain: str = os.environ.get("MODEL_EXPLAIN", "")  # empty = use model_text; set to Ultra only if the eval says so

    # Turn off Nemotron's reasoning for these extraction/rewrite calls (1 = off, 0 = leave on)
    disable_thinking: bool = os.environ.get("DISABLE_THINKING", "1") == "1"

    # Tavily
    tavily_api_key: str = os.environ.get("TAVILY_API_KEY", "")

    # Timeouts (seconds)
    model_timeout_s: float = _f("MODEL_TIMEOUT_S", 45)
    search_timeout_s: float = _f("SEARCH_TIMEOUT_S", 20)
    search_depth: str = os.environ.get("TAVILY_SEARCH_DEPTH", "basic")  # basic = 1 credit, faster; advanced = 2 credits

    # Rule thresholds (all shown to the user in the UI and README)
    price_review_threshold_pct: float = _f("PRICE_REVIEW_THRESHOLD_PCT", 15)
    price_min_comparables: int = int(_f("PRICE_MIN_COMPARABLES", 3))
    price_min_sellers: int = int(_f("PRICE_MIN_SELLERS", 2))
    price_max_age_days: int = int(_f("PRICE_MAX_AGE_DAYS", 90))
    line_rounding_tolerance_inr: float = _f("LINE_ROUNDING_TOLERANCE_INR", 1)
    total_rounding_tolerance_inr: float = _f("TOTAL_ROUNDING_TOLERANCE_INR", 2)
    schedule_window_pct: float = _f("SCHEDULE_WINDOW_PCT", 10)
    normalise_min_confidence: float = _f("NORMALISE_MIN_CONFIDENCE", 0.7)
    estimate_amount_tolerance_pct: float = _f("ESTIMATE_AMOUNT_TOLERANCE_PCT", 10)

    # Paths
    facts_dir: Path = ROOT / "facts"
    cache_db: Path = Path(os.environ.get("CACHE_DB", str(ROOT / "fairfix_public_cache.sqlite")))
    call_log: Path = Path(os.environ.get("CALL_LOG", str(ROOT / "logs" / "calls.jsonl")))

    # Accounts (email + password). AUTH_REQUIRED=0 turns sign-in off (tests, offline work only).
    auth_required: bool = os.environ.get("AUTH_REQUIRED", "1") == "1"
    users_db: Path = Path(os.environ.get("USERS_DB", str(ROOT / "data" / "users.sqlite")))
    database_url: str = os.environ.get("DATABASE_URL", "")          # Postgres for accounts (e.g. Neon); empty = SQLite
    signup_codes: tuple[str, ...] = tuple(c.strip() for c in os.environ.get("SIGNUP_CODES", "").split(",") if c.strip())
    session_days: float = _f("SESSION_DAYS", 7)
    trust_proxy: bool = os.environ.get("TRUST_PROXY", "0") == "1"  # read client IP from X-Forwarded-For
    rate_read_per_hour: int = int(_f("RATE_READ_PER_HOUR", 15))     # photo readings per account
    rate_audit_per_hour: int = int(_f("RATE_AUDIT_PER_HOUR", 40))   # checks + message redrafts per account
    rate_ip_per_hour: int = int(_f("RATE_IP_PER_HOUR", 60))         # all model-backed requests per IP
    login_attempts: int = int(_f("LOGIN_ATTEMPTS", 5))              # failed sign-ins per IP per 15 minutes
    signups_per_hour: int = int(_f("SIGNUPS_PER_HOUR", 5))          # new accounts per IP per hour

    # Fictional sample vehicle for demos/tests. Off in production unless set.
    allow_fictional_sample: bool = os.environ.get("ALLOW_FICTIONAL_SAMPLE", "1") == "1"

    @property
    def explain_model(self) -> str:
        return self.model_explain or self.model_text

    @property
    def models_configured(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def search_configured(self) -> bool:
        return bool(self.tavily_api_key)


settings = Settings()

# Sellers whose listings can count as credible price comparables. Edit to suit;
# the README explains this list. Domains only, matched as suffixes.
CREDIBLE_SELLER_DOMAINS: tuple[str, ...] = tuple(
    d.strip().lower()
    for d in os.environ.get(
        "CREDIBLE_SELLER_DOMAINS",
        "boodmo.com,amazon.in,flipkart.com,marutisuzuki.com,honda2wheelersindia.com,"
        "tatamotors.com,hyundai.com,mahindra.com,heromotocorp.com,tvsmotor.com,bajajauto.com",
    ).split(",")
    if d.strip()
)
