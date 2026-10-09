"""
Centralized logging configuration — Phase 8.

Single place that configures Python logging for the whole backend
process (uvicorn workers included). Called once from app/main.py at
import time, before the FastAPI app is constructed, so every module's
`logging.getLogger(...)` picks up the same format/level.

Kept intentionally simple (stdlib `logging` only, no external
dependency) — this is a hardening pass, not a platform migration.
Swap the handler/formatter here later (e.g. JSON logs for a log
aggregator) without touching any router or service module.
"""

import logging
import re
import sys

from .config import settings

_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)

# `?token=<value>` / `&token=<value>` in any logged URL. Two routes carry a
# bearer credential in the query string because the client cannot send a
# header there: the dashboard WebSocket (a staff session JWT) and the
# patient appointment link (which can cancel or reschedule a booking).
# Uvicorn logs every request line, so without this every session token and
# every patient link was written to the log in plaintext -- and anyone who
# can read the log could replay them until they expire.
_TOKEN_IN_URL = re.compile(r"([?&]token=)[^&\s\"']+")


class RedactTokensFilter(logging.Filter):
    """Replace URL-borne tokens with `[redacted]` before any handler writes them."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a malformed record must still be logged
            return True
        if "token=" in message:
            record.msg = _TOKEN_IN_URL.sub(r"\1[redacted]", message)
            record.args = None
        return True


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers if configure_logging() is called more than
    # once (e.g. re-imported under a test runner or reloader). The redaction
    # filter is still attached to whatever handlers exist, because leaking a
    # token is not acceptable on the second call either.
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(handler)
    for h in root.handlers:
        if not any(isinstance(f, RedactTokensFilter) for f in h.filters):
            h.addFilter(RedactTokensFilter())

    # Uvicorn's own loggers otherwise use their own formatter/handlers;
    # align them so request logs and app logs look consistent.
    for noisy_logger in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy_logger).handlers = []
        logging.getLogger(noisy_logger).propagate = True

    # SQLAlchemy's engine logger is very verbose at INFO — keep it at
    # WARNING unless someone explicitly asks for DEBUG-level app logs.
    if level > logging.DEBUG:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)