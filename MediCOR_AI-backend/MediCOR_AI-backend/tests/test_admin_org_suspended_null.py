"""
Regression test — Super Admin Organizations NULL `suspended` 500 error.

Original error:
    AdminOrgListOut.suspended receives None, but the schema expects bool.

Root cause: Organization.suspended was added after some organizations
already existed. The additive schema-sync ALTER TABLE (app/database.py's
sync_missing_columns) adds the column but does not backfill existing rows,
so pre-existing orgs have `suspended = NULL` in the database — not False.
GET /admin/organizations and GET /admin/organizations/{org_id} passed that
raw NULL straight into a strict `suspended: bool` Pydantic field, which
raised a validation error and surfaced as a 500.

Fix (defense in depth, two layers):
  1. app/routers/admin.py — both endpoints now pass `bool(org.suspended)`
     instead of `org.suspended` when building the response.
  2. app/schemas.py — AdminOrgListOut.suspended and
     AdminOrgDetailOut.suspended both gained a `mode="before"`
     field_validator that coerces None -> False, so the schema itself is
     safe even if something else ever constructs it from a raw ORM object.

This test creates an organization the same way the ORM default normally
would, then manually forces `suspended` back to NULL at the database row
level (bypassing the Python-side `default=False`) to simulate a real
pre-existing row from before the column existed — then asserts both
endpoints return 200 with `suspended: false`, and that an org genuinely
suspended (suspended=True) is still reported correctly (not flipped to
False).

Run with:
    pip install -r requirements.txt
    pytest tests/test_admin_org_suspended_null.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, get_db
from app.routers import admin as admin_router
from app.security import create_super_admin_token, hash_password


@pytest.fixture()
def setup():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        # See note in test_phase8_tenant_isolation.py: ":memory:" gives each
        # pooled connection its own empty database, which breaks any test
        # that crosses a thread boundary via TestClient.
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()

    # A "legacy" org: created normally, then its `suspended` column forced
    # to NULL at the raw SQL level — simulating a row that predates the
    # `suspended` column existing (what sync_missing_columns' additive
    # ALTER TABLE leaves behind for pre-existing rows).
    legacy_org = models.Organization(name="Legacy Clinic (pre-dates suspended column)")
    db.add(legacy_org)
    db.flush()
    db.execute(
        text("UPDATE organizations SET suspended = NULL WHERE id = :id"),
        {"id": legacy_org.id},
    )

    # A genuinely suspended org — must NOT be reported as unsuspended by the fix.
    suspended_org = models.Organization(
        name="Actually Suspended Clinic", suspended=True, suspended_reason="Non-payment"
    )
    db.add(suspended_org)

    # A normal, never-suspended org, created the regular way (suspended=False).
    normal_org = models.Organization(name="Normal Clinic")
    db.add(normal_org)

    admin = models.SuperAdmin(
        email="root@platform.example",
        hashed_password=hash_password("Sup3rSecret!"),
        full_name="Root Admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(legacy_org)
    db.refresh(suspended_org)
    db.refresh(normal_org)
    db.refresh(admin)

    token = create_super_admin_token(admin.id)
    db.close()

    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c, legacy_org.id, suspended_org.id, normal_org.id


def test_list_organizations_does_not_500_on_null_suspended(setup):
    client, legacy_org_id, suspended_org_id, normal_org_id = setup

    r = client.get("/admin/organizations")
    assert r.status_code == 200, r.text

    by_id = {o["id"]: o for o in r.json()}

    # The legacy (NULL) row must be reported as not suspended, not raise.
    assert by_id[legacy_org_id]["suspended"] is False

    # A genuinely suspended org must still be reported as suspended.
    assert by_id[suspended_org_id]["suspended"] is True

    # A normal org is unaffected.
    assert by_id[normal_org_id]["suspended"] is False


def test_get_organization_does_not_500_on_null_suspended(setup):
    client, legacy_org_id, suspended_org_id, normal_org_id = setup

    r = client.get(f"/admin/organizations/{legacy_org_id}")
    assert r.status_code == 200, r.text
    assert r.json()["suspended"] is False

    r = client.get(f"/admin/organizations/{suspended_org_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suspended"] is True
    assert body["suspended_reason"] == "Non-payment"

    r = client.get(f"/admin/organizations/{normal_org_id}")
    assert r.status_code == 200, r.text
    assert r.json()["suspended"] is False
