"""
Shared test helpers.

Until this file existed, each test module bootstrapped itself: stubbing the
`DB_*` environment variables that `app/config.py` requires at import time,
and fixing `sys.path` so `app` and `voice-agent` are importable. Those two
steps are done once here instead, so a new test file needs neither.

The individual modules keep their own copies of that bootstrap — removing
them would be a larger, riskier edit for no behavioural gain, and pytest
imports conftest before any test module, so the environment is already set
by the time those redundant lines run.
"""

import os
import sys

# app/config.py's Settings() has no defaults for these and raises at import
# time without them. No test opens a real connection — every one overrides
# get_db with an in-memory SQLite session — but the import must still work.
os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# `voice-agent` is not a package (it has no __init__.py and a hyphen in the
# name), so its modules are imported flat as `config` / `backend_client`.
_VOICE_AGENT = os.path.join(_BACKEND_ROOT, "voice-agent")
if os.path.isdir(_VOICE_AGENT) and _VOICE_AGENT not in sys.path:
    sys.path.insert(0, _VOICE_AGENT)


class ClientAddressMiddleware:
    """
    Forces the ASGI peer address the app sees.

    Why this exists: the rate-limiting tests need to control the direct peer
    IP, because `app/rate_limit.py` only honours `X-Forwarded-For` when the
    peer is a configured trusted proxy — the whole point of the Phase 0 fix.

    Those tests originally did `TestClient(app, client=(ip, port))`, but that
    keyword only exists in Starlette >= 0.39, while `requirements.txt` pins
    `fastapi==0.115.0`, which requires `starlette<0.39.0`. So the suite could
    not run against the project's own pinned dependencies: 33 tests failed at
    fixture setup with

        TypeError: TestClient.__init__() got an unexpected keyword argument 'client'

    Rewriting `scope["client"]` in a middleware works on every Starlette
    version and keeps the dependency pins untouched, which matters more —
    bumping FastAPI to satisfy a test helper would change the production
    web framework.
    """

    def __init__(self, app, client: tuple[str, int]):
        self.app = app
        self.client = client

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            # Copy rather than mutate: the caller's scope dict may be reused.
            scope = dict(scope)
            scope["client"] = self.client
        await self.app(scope, receive, send)


def make_test_client(app, client: tuple[str, int] | None = None):
    """
    A `TestClient` for `app`, optionally pinned to a specific peer address.

    Use instead of constructing `TestClient(..., client=...)` directly, which
    is not portable across the Starlette versions this project supports.
    """
    from fastapi.testclient import TestClient

    if client is None:
        return TestClient(app)
    return TestClient(ClientAddressMiddleware(app, client))


# ---------------------------------------------------------------------
# Keep the suite off the real database
# ---------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """
    Point the module-global `SessionLocal` at a scratch database, so an
    accidental connection to the configured SQL Server fails instantly
    instead of after a ~30-second ODBC login timeout.

    Every test overrides `get_db`, so no *request* touches the real
    database. But several services deliberately open their OWN session
    rather than taking the request's — `realtime._persist_notification`,
    `llm_service`, `evaluation_service`, `ehr_service`,
    `notification_service`, `scheduler` — precisely so no router has to
    thread one through. Those bypass the override and dial `DB_SERVER`,
    which conftest sets to "unused".

    They already swallow the resulting failure (their documented
    fail-open posture), so the outcome was always "it didn't happen".
    This changes only how long that takes: one file that finalises a few
    calls went from 7 minutes to 5 seconds.

    WHY A SCRATCH ENGINE AND NOT A STUB THAT RAISES

    `SessionLocal()` does not connect — SQLAlchemy sessions are lazy, and
    the connection is made on the first query. In `ehr_service` the
    `db = SessionLocal()` line sits *outside* the try block precisely
    because it cannot fail there. A stub that raises on construction
    would therefore break out at a point real failures never reach, and
    escape the handlers written to contain them. Binding to an empty
    SQLite database instead puts the failure ("no such table") back where
    production puts it — on the first query, inside the existing
    try/except — as an ordinary SQLAlchemyError.

    Both import styles are covered. `evaluation_service`, `llm_service`,
    `rag_service` and `realtime` import SessionLocal *inside* the
    function, so patching `app.database` reaches them. `ehr_service`,
    `notification_service` and `scheduler` bound it at module import, so
    each needs its own attribute patched.

    A test that needs one of these to genuinely succeed rebinds
    SessionLocal to its own session factory, exactly as
    tests/test_hv6_hv7_notifications_callbacks.py already does. That runs
    after this fixture and wins.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import database as app_database

    # Deliberately no create_all(): the tables are absent, so any query
    # fails the way an unreachable database does — immediately, and as a
    # SQLAlchemyError the services already handle.
    scratch = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ScratchSession = sessionmaker(autocommit=False, autoflush=False, bind=scratch)

    monkeypatch.setattr(app_database, "SessionLocal", ScratchSession)

    for module_name in ("ehr_service", "notification_service", "scheduler"):
        module = __import__(f"app.{module_name}", fromlist=["SessionLocal"])
        if hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", ScratchSession)
