"""
Creates a SuperAdmin account directly against the SAME SQL Server
database your FastAPI backend is currently configured for (via
app/config.py + backend/.env) — using the exact same
hash_password()/validate_password_strength() logic the app itself uses
(app/security.py).

There is no self-registration endpoint for Super Admin by design (unlike
POST /auth/login for clinic users) — the first account has to be
created this way. Once you have one, further super admins can be
created either by running this script again or (once you build it)
from an internal-only admin-management UI.

Usage:
    cd backend
    python create_super_admin.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal, Base, engine  # noqa: E402
from app import models  # noqa: E402
from app.security import hash_password, validate_password_strength  # noqa: E402


def main():
    # Make sure the super_admins table exists even on a fresh DB that
    # hasn't had the FastAPI app boot (which normally runs
    # Base.metadata.create_all + sync_missing_columns).
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        email = input("Email: ").strip().lower()
        if not email:
            print("Email is required, aborting.")
            return
        if db.query(models.SuperAdmin).filter(models.SuperAdmin.email == email).first():
            print("A super admin with that email already exists, aborting.")
            return

        full_name = input("Full name: ").strip()
        if not full_name:
            print("Full name is required, aborting.")
            return

        password = input("Password: ").strip()
        error = validate_password_strength(password)
        if error:
            print(f"Weak password: {error}")
            return

        admin = models.SuperAdmin(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print("\n" + "=" * 70)
        print("SUPER ADMIN CREATED")
        print("=" * 70)
        print(f"id={admin.id}  email={admin.email}")
        print("Log in at POST /admin/auth/login with this email/password.")
        print("=" * 70)
    finally:
        db.close()


if __name__ == "__main__":
    main()
