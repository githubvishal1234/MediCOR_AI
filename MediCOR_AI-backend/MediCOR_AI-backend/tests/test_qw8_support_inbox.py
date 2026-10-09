"""
QW-8 — the super-admin support inbox.

`GET /support/tickets` was org-scoped and admin-only, so a clinic could
list its own tickets and nobody could list anyone else's. Submitting a
ticket therefore put it somewhere only a direct database query could
reach — the "black hole" the item describes.

What these tests pin:

  * A super admin sees tickets from **every** clinic in one list, with
    the org name and the submitter attached. That context is the whole
    reason for a separate schema: a platform inbox where every row reads
    "Technical Issue" with no idea whose is useless.
  * The clinic-facing endpoint is **still** org-scoped. Cross-org
    visibility is a super-admin capability, not a widening of the
    existing one.
  * A ticket whose submitting user has since been deleted still lists.
    `SupportTicket.user_id` is nullable and the row outlives the user, so
    a join would have silently dropped it.
  * Status is a closed set, and moving one writes a super-admin audit log
    row like every other cross-org action.

    pytest tests/test_qw8_support_inbox.py -v
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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, rate_limit
from app.database import Base, get_db
from app.routers import admin as admin_router
from app.routers import support as support_router
from app.security import create_super_admin_token, hash_password


@pytest.fixture()
def ctx():
    rate_limit._attempts.clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
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
    app.include_router(support_router.router)
    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()

    north = models.Organization(name="Northside Clinic")
    south = models.Organization(name="Southbank Medical")
    db.add_all([north, south])
    db.flush()

    north_admin = models.User(
        org_id=north.id, email="admin@north.example.com",
        hashed_password=hash_password("Passw0rd!"), full_name="Nora North", role="admin",
    )
    departed = models.User(
        org_id=south.id, email="gone@south.example.com",
        hashed_password=hash_password("Passw0rd!"), full_name="Sam Departed", role="admin",
    )
    db.add_all([north_admin, departed])
    db.flush()

    db.add_all([
        models.SupportTicket(
            org_id=north.id, user_id=north_admin.id, subject="Agent mishears postcodes",
            priority="High", message="Callers give a postcode and it books the wrong branch.",
        ),
        models.SupportTicket(
            org_id=north.id, user_id=north_admin.id, subject="Billing question",
            priority="Normal", message="Which month do overage minutes land in?",
            status="Resolved",
        ),
        models.SupportTicket(
            org_id=south.id, user_id=departed.id, subject="EHR sync stopped",
            priority="Urgent", message="Nothing has reached Epic since Tuesday.",
        ),
    ])

    super_admin = models.SuperAdmin(
        email="ops@medvoice.example.com",
        hashed_password=hash_password("Passw0rd!"),
        full_name="Platform Ops",
    )
    db.add(super_admin)
    db.commit()

    token = {"Authorization": f"Bearer {create_super_admin_token(super_admin.id)}"}

    with TestClient(app) as client:
        yield client, token, db, north.id, south.id, departed.id


def test_the_super_admin_sees_every_clinics_tickets_with_context(ctx):
    client, token, _db, north_id, _south_id, _departed = ctx

    rows = client.get("/admin/support/tickets", headers=token)
    assert rows.status_code == 200, rows.text
    body = rows.json()
    assert len(body) == 3

    org_names = {r["org_name"] for r in body}
    assert org_names == {"Northside Clinic", "Southbank Medical"}

    north_row = next(r for r in body if r["subject"] == "Agent mishears postcodes")
    assert north_row["org_id"] == north_id
    assert north_row["submitted_by"] == "Nora North"
    assert north_row["submitted_by_email"] == "admin@north.example.com"
    assert north_row["status"] == "Open"


def test_the_inbox_filters_by_org_and_status(ctx):
    client, token, _db, north_id, _south_id, _departed = ctx

    by_org = client.get(
        "/admin/support/tickets", params={"org_id": north_id}, headers=token
    ).json()
    assert {r["subject"] for r in by_org} == {
        "Agent mishears postcodes", "Billing question",
    }

    open_only = client.get(
        "/admin/support/tickets", params={"status": "Open"}, headers=token
    ).json()
    assert len(open_only) == 2
    assert all(r["status"] == "Open" for r in open_only)

    both = client.get(
        "/admin/support/tickets",
        params={"org_id": north_id, "status": "Resolved"},
        headers=token,
    ).json()
    assert [r["subject"] for r in both] == ["Billing question"]


def test_a_ticket_outlives_its_submitter(ctx):
    """
    `user_id` is nullable and the ticket row survives the user, so the
    submitter is resolved with a separate lookup rather than a join —
    which would have dropped this row from the inbox entirely.
    """
    client, token, db, _north_id, _south_id, departed_id = ctx

    db.query(models.User).filter(models.User.id == departed_id).delete()
    db.commit()

    body = client.get("/admin/support/tickets", headers=token).json()
    orphan = next(r for r in body if r["subject"] == "EHR sync stopped")
    assert orphan["submitted_by"] is None
    # The clinic it came from is still identifiable, which is what matters.
    assert orphan["org_name"] == "Southbank Medical"


def test_moving_a_ticket_writes_an_audit_row(ctx):
    client, token, db, north_id, _south_id, _departed = ctx

    ticket = (
        db.query(models.SupportTicket)
        .filter(models.SupportTicket.subject == "Agent mishears postcodes")
        .first()
    )

    updated = client.patch(
        f"/admin/support/tickets/{ticket.id}",
        json={"status": "In Progress"},
        headers=token,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "In Progress"
    # Context survives the round trip, so the UI needn't refetch the list.
    assert updated.json()["org_name"] == "Northside Clinic"

    logged = (
        db.query(models.SuperAdminAuditLog)
        .filter(models.SuperAdminAuditLog.action == "support_ticket.status_change")
        .all()
    )
    assert len(logged) == 1
    assert logged[0].target_org_id == north_id
    assert "In Progress" in logged[0].detail


def test_an_unknown_status_is_rejected(ctx):
    """
    A closed set, so counts and filters stay comparable. Without it the
    dropdown and the database drift the first time somebody types one.
    """
    client, token, db, _north_id, _south_id, _departed = ctx

    ticket = db.query(models.SupportTicket).first()
    bad = client.patch(
        f"/admin/support/tickets/{ticket.id}", json={"status": "Closed"}, headers=token
    )
    assert bad.status_code == 422


def test_missing_tickets_and_missing_auth_are_handled(ctx):
    client, token, _db, _north_id, _south_id, _departed = ctx

    assert client.patch(
        "/admin/support/tickets/nope", json={"status": "Resolved"}, headers=token
    ).status_code == 404

    # No super-admin token: the inbox is not reachable at all.
    assert client.get("/admin/support/tickets").status_code == 401


def test_the_clinic_endpoint_is_still_org_scoped(ctx):
    """
    Cross-org visibility is a new super-admin capability, not a widening
    of the existing clinic one. A clinic admin must still see only its own.
    """
    client, _token, db, north_id, _south_id, _departed = ctx

    from app.security import create_access_token

    north_admin = (
        db.query(models.User).filter(models.User.org_id == north_id).first()
    )
    staff = {"Authorization": f"Bearer {create_access_token({"sub": north_admin.id})}"}

    rows = client.get("/support/tickets", headers=staff)
    assert rows.status_code == 200, rows.text
    assert {r["subject"] for r in rows.json()} == {
        "Agent mishears postcodes", "Billing question",
    }
    # And the platform inbox rejects a clinic token outright.
    assert client.get("/admin/support/tickets", headers=staff).status_code == 401
