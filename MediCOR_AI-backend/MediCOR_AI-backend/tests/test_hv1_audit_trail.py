"""
HV-1 — the PHI access and change audit trail.

`audit_log`, `GET /security/audit-log` and the Security page all existed.
The **write path** did not: the table was written by exactly two things,
`seed.py` and `ehr_service.py`, so a clinic's compliance page rendered
five planted demo rows and nothing recorded who had read a patient
record.

What these tests pin, in rough order of how badly each would hurt:

  * **Reading one patient record writes an entry**, including when the
    voice agent does it — that is the per-call PHI access trail.
  * **A 404 writes nothing.** Probing for a patient id belonging to
    another clinic must not leave a row implying it was accessed.
  * **Entries are org-scoped like everything else.** An audit log that
    leaks across tenants is worse than none.
  * **A super admin impersonating a clinic is recorded as the platform**,
    not as the clinic's own admin. Impersonation mints an ordinary staff
    token, which is exactly why this needs a test: without the `imp`
    claim every action we take inside a clinic's data is attributed to
    them.
  * **A failed login is recorded against a known account only.** There is
    no org to file an attempt against an unknown address under, and
    inventing one would make the Security page an enumeration oracle.
  * **A failing audit write never breaks the request it describes.**

    pytest tests/test_hv1_audit_trail.py -v
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

from app import audit_service, models, rate_limit
from app.database import Base, get_db
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import ehr as ehr_router
from app.routers import patients as patients_router
from app.routers import security_compliance as security_router
from app.security import create_super_admin_token, generate_api_key, hash_api_key, hash_password


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
        ehr_router.router,
        patients_router.router,
        security_router.router,
        admin_router.router,
    ):
        app.include_router(r)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal


@pytest.fixture()
def clinic(ctx):
    """A registered clinic, its admin token, and a service key."""
    client, Session = ctx

    r = client.post(
        "/auth/register",
        json={
            "org_name": "Northside Clinic",
            "full_name": "Nora North",
            "email": "admin@north.example.com",
            "password": "Passw0rd!",
        },
    )
    assert r.status_code == 200, r.text
    admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
    org_id = client.get("/auth/me", headers=admin).json()["org_id"]

    raw_key, prefix = generate_api_key()
    db = Session()
    try:
        db.add(models.APIKey(
            org_id=org_id, label="Voice Agent",
            key_prefix=prefix, hashed_key=hash_api_key(raw_key),
        ))
        db.commit()
    finally:
        db.close()

    return client, Session, admin, {"X-API-Key": raw_key}, org_id


def _entries(Session, org_id, action=None):
    db = Session()
    try:
        q = db.query(models.AuditLogEntry).filter(models.AuditLogEntry.org_id == org_id)
        if action:
            q = q.filter(models.AuditLogEntry.action == action)
        return q.order_by(models.AuditLogEntry.occurred_at).all()
    finally:
        db.close()


# ---------- the entry the whole item is about ----------

def test_reading_one_patient_record_is_logged(clinic):
    client, Session, admin, _key, org_id = clinic

    created = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=admin
    )
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    assert client.get(f"/patients/{pid}", headers=admin).status_code == 200

    viewed = _entries(Session, org_id, "patient.viewed")
    assert len(viewed) == 1
    entry = viewed[0]
    assert entry.actor_type == audit_service.ACTOR_USER
    assert "admin@north.example.com" in entry.who
    assert entry.target_type == "patient"
    assert entry.target_id == pid
    # A read changed nothing, which is what "Logged" means here.
    assert entry.status == audit_service.STATUS_LOGGED


def test_the_voice_agent_reading_a_record_is_logged_as_the_agent(clinic):
    """
    The per-call PHI access trail. The agent authenticates through the
    same dual-auth dependency as staff, so this comes for free — but it
    has to be attributed to the AI, not to whichever human happens to be
    logged in.
    """
    client, Session, admin, key, org_id = clinic

    pid = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=admin
    ).json()["id"]

    assert client.get(f"/patients/{pid}", headers=key).status_code == 200

    viewed = _entries(Session, org_id, "patient.viewed")
    assert len(viewed) == 1
    assert viewed[0].actor_type == audit_service.ACTOR_SERVICE
    assert viewed[0].who == "AI Voice Agent"
    # No staff account to point at, and inventing one would be worse.
    assert viewed[0].actor_user_id is None


def test_a_patient_created_by_the_agent_is_attributed_to_the_agent(clinic):
    client, Session, admin, key, org_id = clinic

    created = client.post(
        "/patients", json={"name": "Phoned In", "phone": "+15551111"}, headers=key
    )
    assert created.status_code == 200, created.text

    rows = _entries(Session, org_id, "patient.created")
    assert [r.actor_type for r in rows] == [audit_service.ACTOR_SERVICE]
    assert "MRN" in rows[0].detail


def test_opening_clinical_history_gets_its_own_entry(clinic):
    """
    `/ehr/patients/{id}/history` returns prescriptions and visit notes in
    one response — a deeper read than the patient record itself, so it is
    a distinct action rather than being folded into `patient.viewed`.
    """
    client, Session, admin, _key, org_id = clinic

    pid = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=admin
    ).json()["id"]

    assert client.get(f"/ehr/patients/{pid}/history", headers=admin).status_code == 200

    rows = _entries(Session, org_id, "patient.history_viewed")
    assert len(rows) == 1
    assert rows[0].target_id == pid
    assert rows[0].status == audit_service.STATUS_LOGGED
    # And it did not also write a `patient.viewed`, which would double-count
    # one read as two accesses.
    assert _entries(Session, org_id, "patient.viewed") == []


def test_a_failed_lookup_writes_nothing(clinic):
    """
    The 404 is raised before the entry is written, so probing for ids
    cannot manufacture "this record was accessed" rows — which would both
    pollute the log and hand an attacker a way to fill it.
    """
    client, Session, admin, _key, org_id = clinic

    assert client.get("/patients/does-not-exist", headers=admin).status_code == 404
    assert _entries(Session, org_id, "patient.viewed") == []


def test_changes_record_which_fields_changed_but_not_their_values(clinic):
    """
    An audit log that stores old and new values becomes a second copy of
    the patient record, with a longer retention period and a page more
    people can read. Field names are enough.
    """
    client, Session, admin, _key, org_id = clinic

    pid = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=admin
    ).json()["id"]

    assert client.patch(
        f"/patients/{pid}", json={"phone": "+15559999"}, headers=admin
    ).status_code == 200

    updated = _entries(Session, org_id, "patient.updated")
    assert len(updated) == 1
    assert updated[0].detail == "changed: phone"
    assert "+15559999" not in (updated[0].detail or "")


def test_archiving_is_recorded_before_the_record_goes(clinic):
    client, Session, admin, _key, org_id = clinic

    pid = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=admin
    ).json()["id"]

    assert client.delete(f"/patients/{pid}?purge=true", headers=admin).status_code == 204

    archived = _entries(Session, org_id, "patient.archived")
    assert len(archived) == 1
    assert "Permanently deleted" in archived[0].detail
    # The entry outlives the record it describes — an erasure with no
    # trace of the erasure is not an audit trail.
    assert archived[0].target_id == pid


# ---------- authentication ----------

def test_a_successful_login_is_recorded(clinic):
    client, Session, _admin, _key, org_id = clinic

    r = client.post(
        "/auth/login",
        data={"username": "admin@north.example.com", "password": "Passw0rd!"},
    )
    assert r.status_code == 200, r.text

    logins = _entries(Session, org_id, "auth.login")
    assert len(logins) == 1
    assert logins[0].status == audit_service.STATUS_SUCCESS


def test_a_failed_login_is_recorded_against_a_known_account_only(clinic):
    """
    `audit_log.org_id` is NOT NULL, so an attempt against an address that
    matches no account has no clinic to file it under. Writing it
    somewhere — anywhere — would turn this page into the enumeration
    oracle the identical 401 exists to prevent.
    """
    client, Session, _admin, _key, org_id = clinic

    bad = client.post(
        "/auth/login",
        data={"username": "admin@north.example.com", "password": "wrong"},
    )
    assert bad.status_code == 401

    failures = _entries(Session, org_id, "auth.login_failed")
    assert len(failures) == 1
    assert failures[0].status == audit_service.STATUS_BLOCKED
    assert failures[0].actor_type == audit_service.ACTOR_ANONYMOUS

    stranger = client.post(
        "/auth/login",
        data={"username": "nobody@elsewhere.example.com", "password": "wrong"},
    )
    assert stranger.status_code == 401
    # Still one: the unknown address left no row anywhere.
    db = Session()
    try:
        assert db.query(models.AuditLogEntry).filter(
            models.AuditLogEntry.action == "auth.login_failed"
        ).count() == 1
    finally:
        db.close()


# ---------- impersonation: the compounding problem HV-1 names ----------

def test_impersonation_is_visible_in_the_clinics_own_log(clinic):
    """
    Impersonation mints an ordinary staff token so every route works
    unchanged — and that is precisely why the clinic could never see it.
    Two things have to hold: the platform action itself is mirrored into
    the clinic's log, and everything done during that session is
    attributed to the platform rather than to the clinic's admin.
    """
    client, Session, _admin, _key, org_id = clinic

    db = Session()
    try:
        db.add(models.SuperAdmin(
            email="ops@medvoice.example.com",
            hashed_password=hash_password("Passw0rd!"),
            full_name="Platform Ops",
        ))
        db.commit()
        super_admin_id = db.query(models.SuperAdmin).first().id
    finally:
        db.close()

    platform = {"Authorization": f"Bearer {create_super_admin_token(super_admin_id)}"}

    started = client.post(f"/admin/organizations/{org_id}/impersonate", headers=platform)
    assert started.status_code == 200, started.text
    impersonated = {"Authorization": f"Bearer {started.json()['access_token']}"}

    mirrored = _entries(Session, org_id, "platform.impersonation_started")
    assert len(mirrored) == 1
    assert "ops@medvoice.example.com" in mirrored[0].who

    # Now act inside the clinic's data with that token.
    pid = client.post(
        "/patients", json={"name": "Seen By Vendor", "phone": "+15552222"},
        headers=impersonated,
    ).json()["id"]
    assert client.get(f"/patients/{pid}", headers=impersonated).status_code == 200

    viewed = _entries(Session, org_id, "patient.viewed")
    assert len(viewed) == 1
    assert viewed[0].actor_type == audit_service.ACTOR_PLATFORM
    # Both identities: whose session it really was, and which account it wore.
    assert "ops@medvoice.example.com" in viewed[0].who
    assert "admin@north.example.com" in viewed[0].who


# ---------- the read side ----------

def test_the_log_is_paged_filtered_and_org_scoped(ctx):
    client, Session = ctx

    def register(org_name, email):
        token = client.post("/auth/register", json={
            "org_name": org_name, "full_name": "A Person",
            "email": email, "password": "Passw0rd!",
        }).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        return headers, client.get("/auth/me", headers=headers).json()["org_id"]

    a_headers, a_org = register("Clinic A", "a@a.example.com")
    b_headers, _b_org = register("Clinic B", "b@b.example.com")

    for i in range(3):
        pid = client.post(
            "/patients", json={"name": f"P{i}", "phone": f"+1555000{i}"}, headers=a_headers
        ).json()["id"]
        client.get(f"/patients/{pid}", headers=a_headers)
    client.post("/patients", json={"name": "Theirs"}, headers=b_headers)

    page = client.get("/security/audit-log", params={"page_size": 2}, headers=a_headers).json()
    assert page["page_size"] == 2
    assert len(page["items"]) == 2
    assert page["total"] >= 6  # 3 created + 3 viewed
    # The old endpoint returned a flat list capped at 50 with no way to
    # reach anything older. The page count has to actually cover the total.
    assert page["total_pages"] == -(-page["total"] // 2)
    last = client.get(
        "/security/audit-log",
        params={"page_size": 2, "page": page["total_pages"]},
        headers=a_headers,
    ).json()
    assert last["items"], "the last page must not be empty"

    # Clinic B's activity is invisible from A, and vice versa.
    assert all(i["action"] != "patient.created" or "Theirs" not in (i["detail"] or "")
               for i in page["items"])
    b_page = client.get("/security/audit-log", headers=b_headers).json()
    assert b_page["total"] == 1

    filtered = client.get(
        "/security/audit-log", params={"action": "patient.viewed"}, headers=a_headers
    ).json()
    assert filtered["total"] == 3
    assert {i["action"] for i in filtered["items"]} == {"patient.viewed"}

    actions = client.get("/security/audit-log/actions", headers=a_headers).json()
    assert "patient.viewed" in actions and "patient.created" in actions
    # Only what this clinic has recorded, not the whole vocabulary.
    assert "platform.impersonation_started" not in actions


def test_exporting_the_log_is_itself_logged(clinic):
    """
    A compliance log where taking a copy leaves no trace has a hole
    exactly where an investigator would look.
    """
    client, Session, admin, _key, org_id = clinic

    pid = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=admin
    ).json()["id"]
    client.get(f"/patients/{pid}", headers=admin)

    r = client.get("/security/audit-log/export", headers=admin)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment;" in r.headers["content-disposition"]

    body = r.text.lstrip("﻿")
    assert body.splitlines()[0].startswith("occurred_at,action,status,who")
    assert "patient.viewed" in body

    exports = _entries(Session, org_id, "audit_log.exported")
    assert len(exports) == 1
    assert "exported as CSV" in exports[0].detail


def test_medical_staff_can_read_the_log_but_not_export_it(clinic):
    """
    Hiding the access log from the people it records is how it becomes
    something only the person with the most to hide can read. Bulk
    extraction is a different act, and is admin-only.
    """
    client, Session, admin, _key, org_id = clinic

    db = Session()
    try:
        db.add(models.User(
            org_id=org_id, email="nurse@north.example.com",
            hashed_password=hash_password("Passw0rd!"),
            full_name="Nurse Joy", role=models.UserRole.medical_staff, is_active=True,
        ))
        db.commit()
    finally:
        db.close()

    token = client.post(
        "/auth/login",
        data={"username": "nurse@north.example.com", "password": "Passw0rd!"},
    ).json()["access_token"]
    staff = {"Authorization": f"Bearer {token}"}

    assert client.get("/security/audit-log", headers=staff).status_code == 200
    assert client.get("/security/audit-log/export", headers=staff).status_code == 403


# ---------- the failure mode that must not take the request with it ----------

def test_a_broken_audit_write_does_not_break_the_request(clinic, monkeypatch):
    """
    Fail-open, deliberately: refusing to answer a phone call because an
    audit row would not insert is the wrong trade for a clinic. The
    failure goes to the application log instead of the response.
    """
    client, Session, admin, _key, org_id = clinic

    pid = client.post(
        "/patients", json={"name": "Sam Patient", "phone": "+15550000"}, headers=admin
    ).json()["id"]

    def explode(*_args, **_kwargs):
        raise RuntimeError("audit table is on fire")

    monkeypatch.setattr(audit_service.models, "AuditLogEntry", explode)

    r = client.get(f"/patients/{pid}", headers=admin)
    assert r.status_code == 200
    assert r.json()["id"] == pid


def test_compliance_no_longer_asserts_what_it_cannot_know(clinic):
    """
    `hipaa_verified` defaulted to True in the schema and was never set by
    the router, so the API told every clinic — including one that
    registered a second ago — that it was verified.
    """
    client, _Session, admin, _key, _org_id = clinic

    body = client.get("/security/compliance", headers=admin).json()
    assert body["hipaa_verified"] is False
    assert body["last_security_audit"] == "Not yet audited"
    # And no longer claims a Business Associate Agreement nobody signed.
    assert body["baa_status"] == "Not signed"
    # The count is what makes the page's claim checkable.
    assert body["audit_log_entries"] >= 0
