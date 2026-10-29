"""Test isolation: tests never use real keys and never write to the real call log.

Runs before any app module is imported, so these values win over .env
(python-dotenv does not override variables that are already set).
"""

import os
import tempfile
from pathlib import Path

os.environ["NEBIUS_API_KEY"] = ""
os.environ["TAVILY_API_KEY"] = ""
os.environ["AUTH_REQUIRED"] = "0"   # API tests run signed-out; tests/test_auth.py switches it on
os.environ["SIGNUP_CODES"] = ""
os.environ["CALL_LOG"] = str(Path(tempfile.mkdtemp(prefix="fairfix-tests-")) / "calls.jsonl")
os.environ["USERS_DB"] = str(Path(tempfile.mkdtemp(prefix="fairfix-tests-")) / "users.sqlite")
os.environ["CACHE_DB"] = str(Path(tempfile.mkdtemp(prefix="fairfix-tests-")) / "cache.sqlite")
