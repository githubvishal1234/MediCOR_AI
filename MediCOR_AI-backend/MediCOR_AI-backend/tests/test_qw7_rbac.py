"""
QW-7 — role-aware access control.

`require_admin` existed and worked, but appeared in only 4 of 16 routers.
Any authenticated user — regardless of role — could archive patients,
rewrite the AI's greeting, and delete knowledge-base documents. Meanwhile
the Security page rendered role descriptions for a permission model the
code did not implement.

The rule applied, and what these tests pin:

  * **Reads stay open to medical_staff.** A receptionist has to be able to
    see patients, documents, FAQs and the agent's current settings to do
    their job.

  * **Destructive and AI-configuration writes are admin-only.** Archiving
    a patient, deleting a prescription, and anything that changes what the
    AI says to callers (greeting, routing rules, FAQs, documents, clinic
    info, services) require an admin.

  * **The voice agent must not be affected.** This is the part most
    likely to break silently: the agent authenticates via
    `get_org_context` (API key or org-scoped token), which never runs
    `require_admin`. Every route it touches must still work for it. The
    last test in this file is the regression guard for that — if a future
    change puts `require_admin` on an agent-reachable route, live calls
    break and this test is what catches it.

    pytest tests/test_qw7_rbac.py -v
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
from app.routers import agent_settings as agent_settings_router
from app.routers import auth as auth_router
from app.routers import knowledge_base as kb_router
from app.routers import patients as patients_router
from app.routers import users as users_router
from app.security import generate_api_key, hash_api_key


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
    for r in (
        auth_router.router,
        users_router.router,
        patients_router.router,
        kb_router.router,
        agent_settings_router.router,
    ):
        app.include_router(r)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal


@pytest.fixture()
def roles(ctx):
    """An admin and a medical_staff user in the same clinic, plus a service key."""
    client, Session = ctx

    r = client.post(
        "/auth/register",
        json={
            "org_name": "Clinic A",
            "full_name": "Admin One",
            "email": "admin@clinica.example.com",
            "password": "Passw0rd!",
        },
    )
    assert r.status_code == 200, r.text
    admin = {"Authorization": f"Bearer {r.json()['access_token']}"}

    invite = client.post(
        "/users/invite",
        json={
            "email": "nurse@clinica.example.com",
            "full_name": "Nurse Joy",
            "role": "medical_staff",
        },
        headers=admin,
    ).json()
    accepted = client.post(
        "/auth/accept-invite",
        json={"token": invite["invite_token"], "password": "N3wPassw0rd!"},
    ).json()
    staff = {"Authorization": f"Bearer {accepted['access_token']}"}

    org_id = client.get("/auth/me", headers=admin).json()["org_id"]

    # A service key, exactly as the voice agent would present it.
    raw_key, prefix = generate_api_key()
    db = Session()
    try:
        db.add(
            models.APIKey(
                org_id=org_id,
                label="Voice Agent",
                key_prefix=prefix,
                hashed_key=hash_api_key(raw_key),
            )
        )
        db.commit()
    finally:
        db.close()

    return client, admin, staff, {"X-API-Key": raw_key}, org_id


# ---------- reads stay open to medical_staff ----------

@pytest.mark.parametrize(
    "path",
    [
        "/patients",
        "/knowledge-base/documents",
        "/knowledge-base/sources",
        "/knowledge-base/faqs",
        "/knowledge-base/services",
        "/knowledge-base/clinic-info",
        "/agent-settings",
        "/agent-settings/routing-rules",
    ],
)
def test_medical_staff_can_still_read(roles, path):
    client, _admin, staff, _key, _org = roles
    assert client.get(path, headers=staff).status_code == 200, path


def test_medical_staff_can_still_do_clinical_work(roles):
    """Creating and editing patients is the job — it must not need admin."""
    client, _admin, staff, _key, _org = roles

    created = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=staff
    )
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    assert client.patch(
        f"/patients/{pid}", json={"phone": "+15550001"}, headers=staff
    ).status_code == 200
    assert client.post(
        f"/patients/{pid}/prescriptions",
        json={"name": "Amoxicillin", "detail": "500mg"},
        headers=staff,
    ).status_code == 200


# ---------- destructive / AI-config writes are admin-only ----------

def test_medical_staff_cannot_archive_a_patient(roles):
    client, admin, staff, _key, _org = roles
    pid = client.post("/patients", json={"name": "Sam Patient"}, headers=admin).json()["id"]

    assert client.delete(f"/patients/{pid}", headers=staff).status_code == 403
    # ...and an admin still can.
    assert client.delete(f"/patients/{pid}", headers=admin).status_code == 204


def test_medical_staff_cannot_rewrite_what_the_ai_says(roles):
    """The greeting is spoken to patients on live calls — admin only."""
    client, admin, staff, _key, _org = roles

    payload = {"voice_profile": "anushka", "greeting_script": "Hacked greeting."}
    assert client.put("/agent-settings", json=payload, headers=staff).status_code == 403
    assert client.put("/agent-settings", json=payload, headers=admin).status_code == 200


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/knowledge-base/faqs", {"question": "Q?", "answer": "A."}),
        ("post", "/knowledge-base/sources", {"url": "https://example.com"}),
        ("post", "/knowledge-base/services", {"name": "Consult", "fee_label": "$100"}),
        ("put", "/knowledge-base/clinic-info", {"phone": "+15550000"}),
        ("post", "/agent-settings/routing-rules", {"title": "Urgent", "detail": "Nurse"}),
    ],
)
def test_medical_staff_cannot_change_knowledge_the_ai_reads_out(roles, method, path, body):
    client, admin, staff, _key, _org = roles
    assert getattr(client, method)(path, json=body, headers=staff).status_code == 403
    assert getattr(client, method)(path, json=body, headers=admin).status_code in (200, 201)


# ---------- the voice agent must be unaffected ----------

def test_the_voice_agent_still_reaches_everything_it_needs(roles):
    """
    Regression guard for the one way this change could break production.

    The agent authenticates with X-API-Key through `get_org_context`, which
    does not consult roles at all. If someone later moves one of these
    routes behind `require_admin`, the agent starts getting 403s mid-call
    and this test fails first.
    """
    client, admin, _staff, key, _org = roles

    # Reads the agent performs while answering a caller.
    for path in (
        "/patients",
        "/knowledge-base/clinic-info",
        "/knowledge-base/services",
        "/knowledge-base/faqs",
        "/knowledge-base/ask?q=opening+hours",
    ):
        assert client.get(path, headers=key).status_code == 200, path

    # Registering a new caller as a patient — a write, and the agent must
    # keep being able to do it without being an "admin".
    created = client.post(
        "/patients", json={"name": "Caller From Phone", "phone": "+15551234"}, headers=key
    )
    assert created.status_code == 200, created.text
    assert client.get(f"/patients/{created.json()['id']}", headers=key).status_code == 200


def test_a_service_key_still_cannot_reach_admin_only_routes(roles):
    """
    The flip side: broad agent access must not become admin access.

    These routes use `require_admin`, which runs `get_current_user` — and
    that path rejects an API key outright, since a key resolves an org,
    not a user.
    """
    client, _admin, _staff, key, _org = roles
    assert client.put(
        "/agent-settings", json={"greeting_script": "x"}, headers=key
    ).status_code == 401
    assert client.post(
        "/knowledge-base/faqs", json={"question": "Q?", "answer": "A."}, headers=key
    ).status_code == 401
