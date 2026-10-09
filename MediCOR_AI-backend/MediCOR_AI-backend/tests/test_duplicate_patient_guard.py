"""
Registering somebody who is already registered.

    pytest tests/test_duplicate_patient_guard.py -v

WHAT THIS PREVENTS

The production database held two "Krishna" rows: one number, no date of
birth, created eighty minutes apart. A returning caller rang, the
caller-id lookup failed to match them because Twilio sends E.164 and the
record held a bare national number, the agent concluded they were new,
and registered them again.

That search is fixed (`tests/test_patient_phone_matching.py`). This is
the other half: the prompt telling the model to look somebody up first is
a request, not a rule, and `POST /patients` would create the second
record every time it was asked to.

THE TWO CALLERS WANT OPPOSITE THINGS

`POST /patients` is dual-auth. Staff are at a screen and can act on being
told; the agent is mid-conversation and cannot. So the endpoint answers
differently, following the precedent already in the same function, where
plan limits are enforced for staff and deliberately not for the agent:

  * staff       409, naming the existing record so it can be opened
  * the agent   the existing patient, so the call continues with one
                record instead of two

WHAT IS DELIBERATELY NOT A DUPLICATE

The same number with a different name. Households share a handset, and a
clinic must be able to register a spouse or a child on one line -- the
patient search returns both of them for one number, and the agent's
"exactly one match" rule is built on that being possible.
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import ehr_service, models, rate_limit  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import patients as patients_router  # noqa: E402
from app.security import generate_api_key, hash_api_key  # noqa: E402


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()
    monkeypatch.setattr(ehr_service, "schedule_patient_sync", lambda *a, **k: None)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Testing = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = Testing()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.include_router(patients_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "HealthLink Clinic", "full_name": "Nora North",
            "email": "admin@healthlink.example.com", "password": "Passw0rd!",
        })
        staff = {"Authorization": f"Bearer {r.json()['access_token']}"}
        org_id = client.get("/auth/me", headers=staff).json()["org_id"]

        raw_key, prefix = generate_api_key()
        s = Testing()
        try:
            s.add(models.APIKey(
                org_id=org_id, label="Voice Agent",
                key_prefix=prefix, hashed_key=hash_api_key(raw_key),
            ))
            s.commit()
        finally:
            s.close()

        # `agent` authenticates as a service, exactly as the voice agent's
        # `register_patient` tool does.
        yield client, staff, {"X-API-Key": raw_key}, Testing


def register(client, headers, name, phone, **extra):
    body = {"name": name, "phone": phone}
    body.update(extra)
    return client.post("/patients", json=body, headers=headers)


def count_patients(Session, name=None):
    db = Session()
    try:
        q = db.query(models.Patient)
        if name:
            q = q.filter(models.Patient.name == name)
        return q.count()
    finally:
        db.close()


# =====================================================================
# The agent, mid-call
# =====================================================================


class TestTheAgentGetsTheExistingRecord:
    def test_registering_a_known_caller_returns_them_instead_of_duplicating(self, ctx):
        """
        THE regression test. This exact sequence produced the two
        "Krishna" rows in production.
        """
        client, staff, agent, Session = ctx
        first = register(client, agent, "Krishna", "8688460308").json()

        second = register(client, agent, "Krishna", "8688460308")

        assert second.status_code == 200, second.text
        assert second.json()["id"] == first["id"]
        assert second.json()["mrn"] == first["mrn"]
        assert count_patients(Session, "Krishna") == 1

    def test_the_call_is_never_failed_over_this(self, ctx):
        """
        The agent must always come away with a usable patient id. A 4xx
        here would strand a caller who has just given their name and
        number.
        """
        client, staff, agent, Session = ctx
        register(client, agent, "Krishna", "8688460308")

        again = register(client, agent, "Krishna", "8688460308")
        assert again.status_code == 200
        assert again.json()["id"]

    def test_the_number_the_caller_gave_is_matched_however_it_is_written(self, ctx):
        """
        The whole point: Twilio delivers E.164, the record holds a bare
        national number. Those are one line.
        """
        client, staff, agent, Session = ctx
        first = register(client, agent, "Krishna", "8688460308").json()

        for spelling in ("+918688460308", "+91 86884 60308", "008918688460308"):
            again = register(client, agent, "Krishna", spelling)
            assert again.json()["id"] == first["id"], spelling
        assert count_patients(Session, "Krishna") == 1

    def test_the_name_is_matched_case_and_spacing_insensitively(self, ctx):
        client, staff, agent, Session = ctx
        first = register(client, agent, "Krishna Kumar", "8688460308").json()

        for spelling in ("krishna kumar", "  Krishna   Kumar  ", "KRISHNA KUMAR"):
            assert register(client, agent, spelling, "8688460308").json()["id"] == first["id"]
        assert count_patients(Session) == 1

    def test_returning_an_existing_patient_is_not_logged_as_a_creation(self, ctx):
        """
        `patient.created` would be a lie -- nothing was created. The
        clinic's audit log is the page it would show an auditor.
        """
        client, staff, agent, Session = ctx
        register(client, agent, "Krishna", "8688460308")
        register(client, agent, "Krishna", "8688460308")

        db = Session()
        try:
            actions = [r.action for r in db.query(models.AuditLogEntry).all()]
        finally:
            db.close()
        assert actions.count("patient.created") == 1
        assert "patient.viewed" in actions


# =====================================================================
# Staff, at a screen
# =====================================================================


class TestStaffAreToldRatherThanSilentlyRedirected:
    def test_a_duplicate_is_refused_with_the_existing_record_named(self, ctx):
        """
        Silently handing back the existing record would leave a
        receptionist believing they had created a new one. They can act
        on being told; the message is written for them and the patient
        modal surfaces it.
        """
        client, staff, agent, Session = ctx
        first = register(client, staff, "Krishna", "8688460308").json()

        again = register(client, staff, "Krishna", "8688460308")

        assert again.status_code == 409
        detail = again.json()["detail"]
        assert "Krishna" in detail
        assert first["mrn"] in detail
        assert "Open that record" in detail
        assert count_patients(Session, "Krishna") == 1

    def test_a_genuinely_new_patient_is_unaffected(self, ctx):
        client, staff, agent, Session = ctx
        assert register(client, staff, "Krishna", "8688460308").status_code == 200
        assert register(client, staff, "Vishal", "9652359002").status_code == 200
        assert count_patients(Session) == 2


# =====================================================================
# What must NOT be treated as a duplicate
# =====================================================================


class TestSharedHandsets:
    def test_two_people_on_one_number_are_two_patients(self, ctx):
        """
        Households share a handset. Blocking on the number alone would
        stop a clinic registering a spouse or a child -- and the patient
        search deliberately returns both of them for one number, which is
        what makes the agent's "exactly one match" rule protective.
        """
        client, staff, agent, Session = ctx
        assert register(client, staff, "Krishna", "8688460308").status_code == 200
        assert register(client, staff, "Krishna's Mother", "8688460308").status_code == 200
        assert count_patients(Session) == 2

    def test_the_same_name_on_a_different_number_is_a_different_patient(self, ctx):
        client, staff, agent, Session = ctx
        assert register(client, staff, "Krishna", "8688460308").status_code == 200
        assert register(client, staff, "Krishna", "9652359002").status_code == 200
        assert count_patients(Session) == 2

    def test_a_patient_with_no_phone_can_always_be_registered(self, ctx):
        """
        A walk-in with no number to give. There is nothing to match on,
        so nothing is blocked.
        """
        client, staff, agent, Session = ctx
        assert client.post("/patients", json={"name": "Anon One"},
                           headers=staff).status_code == 200
        assert client.post("/patients", json={"name": "Anon Two"},
                           headers=staff).status_code == 200
        assert count_patients(Session) == 2

    def test_names_are_not_matched_fuzzily(self, ctx):
        """
        "Krishna Kumar" and "K. Kumar" may well be one person, and
        deciding that they are is a clinical guess presented as a fact --
        the same reasoning that keeps repeat-prescription matching to an
        exact medication name.
        """
        client, staff, agent, Session = ctx
        assert register(client, staff, "Krishna Kumar", "8688460308").status_code == 200
        assert register(client, staff, "K. Kumar", "8688460308").status_code == 200
        assert count_patients(Session) == 2


class TestArchivedRecords:
    def test_an_archived_patient_does_not_block_a_new_registration(self, ctx):
        """
        An archived row was deliberately taken off the roster, and
        `merge_duplicate_patients.py` archives the losing side of a merge
        -- so matching them would hand back a record that has already
        been merged away.
        """
        client, staff, agent, Session = ctx
        created = register(client, staff, "Krishna", "8688460308").json()

        db = Session()
        try:
            row = db.query(models.Patient).filter(
                models.Patient.id == created["id"]).first()
            row.status = "Archived"
            db.commit()
        finally:
            db.close()

        again = register(client, staff, "Krishna", "8688460308")
        assert again.status_code == 200
        assert again.json()["id"] != created["id"]


# =====================================================================
# Tenancy
# =====================================================================


class TestTheGuardStaysInsideTheOrg:
    def test_another_clinics_patient_does_not_block_registration(self, ctx):
        """
        Two clinics can each have a patient of the same name on the same
        number, and neither should be able to detect the other's. A guard
        that searched across orgs would both break registration and leak
        the fact that a named person is a patient elsewhere.
        """
        client, staff, agent, Session = ctx
        register(client, staff, "Krishna", "8688460308")

        r = client.post("/auth/register", json={
            "org_name": "Other Clinic", "full_name": "Other Admin",
            "email": "admin@other.example.com", "password": "Passw0rd!",
        })
        other = {"Authorization": f"Bearer {r.json()['access_token']}"}

        assert register(client, other, "Krishna", "8688460308").status_code == 200
        assert count_patients(Session, "Krishna") == 2
