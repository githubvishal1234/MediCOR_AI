"""
Diagnostic script: verifies whether the API key configured in
voice-agent/.env matches a live (non-revoked) row in the api_keys table
of the SQL Server database the backend is actually using.

Run this from the `backend/` folder (same folder as this file), with the
same Python environment/venv the FastAPI backend uses, so it picks up
the identical settings and DB connection as the running app:

    cd backend
    python diagnose_api_key.py

It never prints the raw key, its hash, or any other secret in full —
only YES/NO, lengths, and first/last 4 characters, which is enough to
diagnose the mismatch safely.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402
from app.security import hash_api_key  # noqa: E402


def load_env_value(path: str, key: str) -> str:
    """Minimal .env reader — avoids depending on python-dotenv here."""
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return ""


def mask(v: str) -> str:
    if not v:
        return "(empty)"
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:4]}...{v[-4:]} (len={len(v)})"


def main():
    print("=" * 70)
    print("STEP 1 — Confirm which SQL Server database the BACKEND connects to")
    print("=" * 70)
    print(f"DB_SERVER : {settings.db_server}")
    print(f"DB_PORT   : {settings.db_port}")
    print(f"DB_NAME   : {settings.db_name}")
    print(f"DB_USER   : {settings.db_user}")
    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT DB_NAME()").fetchone()
        print(f"Actual connected database (SELECT DB_NAME()): {row[0]}")

    print()
    print("=" * 70)
    print("STEP 2 — Read BACKEND_API_KEY from voice-agent/.env")
    print("=" * 70)
    va_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice-agent", ".env")
    raw_key = load_env_value(va_env_path, "BACKEND_API_KEY")
    print(f"voice-agent/.env path checked: {va_env_path}")
    print(f"BACKEND_API_KEY found        : {'YES' if raw_key else 'NO'}")
    print(f"BACKEND_API_KEY value        : {mask(raw_key)}")
    print(f"Starts with 'sk_live_'       : {raw_key.startswith('sk_live_') if raw_key else 'n/a'}")

    if not raw_key:
        print("\nRESULT: BACKEND_API_KEY is empty or missing — this alone causes the 401.")
        return

    hashed = hash_api_key(raw_key)

    print()
    print("=" * 70)
    print("STEP 3 — Look up this key's hash in api_keys (same hash fn as deps.py)")
    print("=" * 70)
    db = SessionLocal()
    try:
        total_keys = db.query(models.APIKey).count()
        print(f"Total rows currently in api_keys table: {total_keys}")

        exact = db.query(models.APIKey).filter(models.APIKey.hashed_key == hashed).first()
        if exact is None:
            print("MATCH FOUND: NO")
            print(
                "-> No row in api_keys has this key's hash at all. This key was "
                "never created against THIS database — most likely it was "
                "generated back when the app pointed at SQLite, and never "
                "re-created after the SQL Server migration."
            )
        else:
            print("MATCH FOUND: YES")
            print(f"   id          : {exact.id}")
            print(f"   org_id      : {exact.org_id}")
            print(f"   label       : {exact.label}")
            print(f"   key_prefix  : {exact.key_prefix}")
            print(f"   environment : {exact.environment}")
            print(f"   created_at  : {exact.created_at}")
            print(f"   revoked     : {exact.revoked}")
            if exact.revoked:
                print(
                    "\nRESULT: The key exists but is REVOKED. That's why "
                    "get_org_context() rejects it (query filters revoked == False)."
                )
            else:
                print(
                    "\nRESULT: The key is valid and not revoked — if you're still "
                    "seeing 401s, double-check the voice agent process actually "
                    "picked up this exact .env value (e.g. restart it fully)."
                )

        print()
        print("All non-revoked keys currently usable against this database:")
        rows = db.query(models.APIKey).filter(models.APIKey.revoked == False).all()  # noqa: E712
        if not rows:
            print("  (none — the api_keys table has no active keys at all)")
        for r in rows:
            print(f"  - id={r.id} org_id={r.org_id} label={r.label!r} prefix={r.key_prefix} created_at={r.created_at}")
    finally:
        db.close()


if __name__ == "__main__":
    main()