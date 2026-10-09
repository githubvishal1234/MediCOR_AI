"""
Creates a brand-new service API key directly against the SAME SQL Server
database your FastAPI backend is currently configured for (via app/config.py
+ backend/.env) — using the exact same generate_api_key()/hash_api_key()
logic the app itself uses (app/security.py). This guarantees the key will
be accepted by get_org_context() in app/deps.py.

Equivalent to using the dashboard's "EHR Integration -> API Keys -> Create
API Key" button, just runnable from the command line.

Usage:
    cd backend
    python create_service_api_key.py

It will list existing organizations, ask you to pick one, then print the
new plaintext key ONCE. Copy it into voice-agent/.env as BACKEND_API_KEY
immediately — it is not recoverable afterwards (only its hash is stored).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.security import generate_api_key, hash_api_key  # noqa: E402


def main():
    db = SessionLocal()
    try:
        orgs = db.query(models.Organization).all()
        if not orgs:
            print("No organizations found in this database. Create one first (register a user).")
            return

        print("Organizations in this database:")
        for i, org in enumerate(orgs):
            print(f"  [{i}] id={org.id}  name={org.name!r}")

        choice = input(f"\nPick an organization [0-{len(orgs) - 1}]: ").strip()
        try:
            org = orgs[int(choice)]
        except (ValueError, IndexError):
            print("Invalid selection, aborting.")
            return

        label = input("Label for this key (e.g. 'voice-agent production'): ").strip() or "voice-agent"

        plaintext, prefix = generate_api_key()
        key_row = models.APIKey(
            org_id=org.id,
            label=label,
            environment="production",
            key_prefix=prefix,
            hashed_key=hash_api_key(plaintext),
        )
        db.add(key_row)
        db.commit()
        db.refresh(key_row)

        print("\n" + "=" * 70)
        print("NEW API KEY CREATED — copy this into voice-agent/.env now.")
        print("It will not be shown again (only its hash is stored in the DB).")
        print("=" * 70)
        print(f"BACKEND_API_KEY={plaintext}")
        print("=" * 70)
        print(f"org_id={org.id}  key_id={key_row.id}  prefix={prefix}")
    finally:
        db.close()


if __name__ == "__main__":
    main()