"""
HV-2 — clinic staff & invite management.

Before this feature the only user-creating endpoint was POST /auth/register,
which creates a new *organization*, so a clinic could not add a second
person: a receptionist who signed up got their own separate clinic. These
tests pin the invite lifecycle and, more importantly, the guards around it.

Three things matter more than the happy path and are tested hardest:

  1. **Tenant isolation.** An admin at clinic B must not be able to see,
     modify, or deactivate a user at clinic A — and must get a 404, not a
     403, so the endpoint cannot be used to confirm that an id exists.

  2. **Lockout safety.** A clinic that demotes or deactivates its last
     admin has no self-service way back. Those transitions are refused.

  3. **Invite token handling.** Single-use, expiring, stored only as a
     SHA-256 hash, and invalidated when the invite is re-sent or the user
     is deactivated.

Runs against throwaway in-memory SQLite; never touches the configured
SQL Server.

    pytest tests/test_hv2_staff_management.py -v
"""

import os
import sys
from datetime import datetime, timedelta

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
from app.routers import auth as auth_router
from app.routers import users as users_router
from app.security import hash_invite_token


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
    app.include_router(auth_router.router)
    app.include_router(users_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal


def _register(client, org="Clinic A", email="admin@clinica.example.com", password="Passw0rd!"):
    r = client.post(
        "/auth/register",
        json={"org_name": org, "full_name": "Admin One", "email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _invite(client, headers, email="nurse@clinica.example.com", role="medical_staff", name="Nurse Joy"):
    r = client.post(
        "/users/invite",
        json={"email": email, "full_name": name, "role": role},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------- happy path ----------

def test_invite_creates_an_inactive_user_who_cannot_yet_log_in(ctx):
    client, _ = ctx
    headers = _register(client)
    body = _invite(client, headers)

    assert body["user"]["is_active"] is False
    assert body["user"]["invite_pending"] is True
    assert body["user"]["role"] == "medical_staff"
    assert body["invite_token"]

    # The unusable placeholder password must not be guessable, and an
    # inactive user is refused regardless.
    r = client.post(
        "/auth/login", data={"username": "nurse@clinica.example.com", "password": "Passw0rd!"}
    )
    assert r.status_code == 401


def test_accepting_an_invite_activates_the_account_and_returns_a_token(ctx):
    client, Session = ctx
    headers = _register(client)
    token = _invite(client, headers)["invite_token"]

    r = client.post("/auth/accept-invite", json={"token": token, "password": "N3wPassw0rd!"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]

    # The new user can now log in, and lands in the inviting org.
    r = client.post(
        "/auth/login", data={"username": "nurse@clinica.example.com", "password": "N3wPassw0rd!"}
    )
    assert r.status_code == 200

    me = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    ).json()
    assert me["role"] == "medical_staff"
    assert me["org_name"] == "Clinic A"


def test_the_raw_invite_token_is_never_stored(ctx):
    client, Session = ctx
    headers = _register(client)
    token = _invite(client, headers)["invite_token"]

    db = Session()
    try:
        user = db.query(models.User).filter(models.User.email == "nurse@clinica.example.com").first()
        assert user.invite_token_hash == hash_invite_token(token)
        assert token not in (user.invite_token_hash or "")
    finally:
        db.close()


# ---------- invite token handling ----------

def test_an_invite_token_is_single_use(ctx):
    client, _ = ctx
    headers = _register(client)
    token = _invite(client, headers)["invite_token"]

    assert client.post("/auth/accept-invite", json={"token": token, "password": "N3wPassw0rd!"}).status_code == 200
    # Replaying the link must not let anyone reset the password later.
    r = client.post("/auth/accept-invite", json={"token": token, "password": "Attack3r!"})
    assert r.status_code == 400


def test_an_expired_invite_is_refused(ctx):
    client, Session = ctx
    headers = _register(client)
    token = _invite(client, headers)["invite_token"]

    db = Session()
    try:
        user = db.query(models.User).filter(models.User.email == "nurse@clinica.example.com").first()
        user.invite_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    r = client.post("/auth/accept-invite", json={"token": token, "password": "N3wPassw0rd!"})
    assert r.status_code == 400


def test_resending_an_invite_invalidates_the_previous_link(ctx):
    client, _ = ctx
    headers = _register(client)
    first = _invite(client, headers)
    user_id = first["user"]["id"]

    second = client.post(f"/users/{user_id}/resend-invite", headers=headers).json()
    assert second["invite_token"] != first["invite_token"]

    # A link that leaked before the resend must stop working.
    assert client.post(
        "/auth/accept-invite", json={"token": first["invite_token"], "password": "N3wPassw0rd!"}
    ).status_code == 400
    assert client.post(
        "/auth/accept-invite", json={"token": second["invite_token"], "password": "N3wPassw0rd!"}
    ).status_code == 200


def test_deactivating_a_pending_user_kills_their_invite(ctx):
    client, _ = ctx
    headers = _register(client)
    body = _invite(client, headers)

    assert client.delete(f"/users/{body['user']['id']}", headers=headers).status_code == 204
    r = client.post(
        "/auth/accept-invite", json={"token": body["invite_token"], "password": "N3wPassw0rd!"}
    )
    assert r.status_code == 400


def test_a_garbage_token_is_refused(ctx):
    client, _ = ctx
    _register(client)
    r = client.post("/auth/accept-invite", json={"token": "not-a-real-token", "password": "N3wPassw0rd!"})
    assert r.status_code == 400


# ---------- authorization ----------

def test_medical_staff_cannot_manage_the_team(ctx):
    client, _ = ctx
    admin_headers = _register(client)
    token = _invite(client, admin_headers)["invite_token"]
    accepted = client.post(
        "/auth/accept-invite", json={"token": token, "password": "N3wPassw0rd!"}
    ).json()
    staff_headers = {"Authorization": f"Bearer {accepted['access_token']}"}

    assert client.get("/users", headers=staff_headers).status_code == 403
    assert client.post(
        "/users/invite",
        json={"email": "x@clinica.example.com", "full_name": "X", "role": "admin"},
        headers=staff_headers,
    ).status_code == 403


def test_duplicate_email_in_the_same_org_is_refused(ctx):
    client, _ = ctx
    headers = _register(client)
    _invite(client, headers)
    r = client.post(
        "/users/invite",
        json={"email": "nurse@clinica.example.com", "full_name": "Someone Else", "role": "medical_staff"},
        headers=headers,
    )
    assert r.status_code == 409
    assert "already on your team" in r.json()["detail"]


def test_an_email_taken_at_another_clinic_does_not_leak_that_fact(ctx):
    client, _ = ctx
    _register(client, org="Clinic A", email="admin@clinica.example.com")
    b_headers = _register(client, org="Clinic B", email="admin@clinicb.example.com")

    r = client.post(
        "/users/invite",
        json={"email": "admin@clinica.example.com", "full_name": "Poach Attempt", "role": "admin"},
        headers=b_headers,
    )
    assert r.status_code == 409
    # Must not confirm that this person has an account at another clinic.
    assert "already on your team" not in r.json()["detail"]
    assert "not available" in r.json()["detail"]


# ---------- tenant isolation ----------

def test_one_clinic_cannot_see_or_touch_another_clinics_users(ctx):
    client, _ = ctx
    a_headers = _register(client, org="Clinic A", email="admin@clinica.example.com")
    a_user = _invite(client, a_headers)["user"]
    b_headers = _register(client, org="Clinic B", email="admin@clinicb.example.com")

    # B's team list contains only B.
    team = client.get("/users", headers=b_headers).json()
    assert [u["email"] for u in team] == ["admin@clinicb.example.com"]

    # 404, not 403 — B must not be able to confirm A's user id exists.
    assert client.patch(
        f"/users/{a_user['id']}", json={"role": "admin"}, headers=b_headers
    ).status_code == 404
    assert client.delete(f"/users/{a_user['id']}", headers=b_headers).status_code == 404
    assert client.post(
        f"/users/{a_user['id']}/resend-invite", headers=b_headers
    ).status_code == 404


# ---------- lockout guards ----------

def test_an_admin_cannot_demote_or_deactivate_themselves(ctx):
    client, _ = ctx
    headers = _register(client)
    me = client.get("/auth/me", headers=headers).json()

    assert client.patch(
        f"/users/{me['id']}", json={"role": "medical_staff"}, headers=headers
    ).status_code == 400
    assert client.patch(
        f"/users/{me['id']}", json={"is_active": False}, headers=headers
    ).status_code == 400
    assert client.delete(f"/users/{me['id']}", headers=headers).status_code == 400


def test_the_last_admin_cannot_be_removed_by_another_admin(ctx):
    client, _ = ctx
    first = _register(client)
    # Promote a second admin, then have them try to strip the first.
    token = _invite(client, first, email="second@clinica.example.com", role="admin")["invite_token"]
    accepted = client.post(
        "/auth/accept-invite", json={"token": token, "password": "N3wPassw0rd!"}
    ).json()
    second = {"Authorization": f"Bearer {accepted['access_token']}"}

    first_id = client.get("/auth/me", headers=first).json()["id"]

    # Two admins exist, so demoting one is allowed.
    assert client.patch(
        f"/users/{first_id}", json={"role": "medical_staff"}, headers=second
    ).status_code == 200

    # Now only `second` is an admin, and they cannot remove themselves.
    second_id = client.get("/auth/me", headers=second).json()["id"]
    assert client.delete(f"/users/{second_id}", headers=second).status_code == 400


def test_reactivating_a_never_accepted_invite_is_refused(ctx):
    client, _ = ctx
    headers = _register(client)
    user_id = _invite(client, headers)["user"]["id"]

    # Flipping is_active on a pending invite would create an account with
    # an unusable password and no way in — resend the invite instead.
    r = client.patch(f"/users/{user_id}", json={"is_active": True}, headers=headers)
    assert r.status_code == 400
    assert "resend" in r.json()["detail"].lower()
