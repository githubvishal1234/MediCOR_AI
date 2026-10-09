"""
Patient language memory (F5).

The voice agent already detected the caller's language per turn and
switched Sarvam TTS to match — then discarded it when the call ended. A
patient who had called ten times in Telugu was greeted in English on the
eleventh, every time.

What these pin:

  * the detection is **persisted** onto the patient, so the next call can
    open in the right language;
  * an unsupported tag can never be stored, because the stored value is
    fed straight back to Sarvam TTS;
  * a silent call does **not** overwrite a language we already know —
    that is the difference between adapting and forgetting;
  * the propagation is org-scoped. A call carrying another clinic's
    `patient_id` must not reach that patient, which is the exact shape of
    the one cross-org IDOR this codebase has had.

    pytest tests/test_language_memory.py -v
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
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models, rate_limit, schemas  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import calls as calls_router  # noqa: E402
from app.routers import patients as patients_router  # noqa: E402


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()

    # NOTE: the services on the call-finalisation path that open their own
    # SessionLocal (llm_service, realtime._persist_notification) are
    # already neutralised for the whole suite by the `_no_real_database`
    # fixture in conftest.py, which is what stops them dialling the real
    # SQL Server. Nothing extra is needed here.

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
    for r in (auth_router.router, calls_router.router, patients_router.router):
        app.include_router(r)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, Testing


def _register(client, org_name, email):
    r = client.post("/auth/register", json={
        "org_name": org_name, "full_name": "Owner",
        "email": email, "password": "Passw0rd!",
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def clinic(ctx):
    client, Testing = ctx
    admin = _register(client, "Northside Clinic", "admin@north.example.com")
    return client, Testing, admin


def _patient(client, headers, name="Anitha Rao"):
    r = client.post("/patients", json={"name": name, "phone": "+919000000001"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _call(client, headers, patient_id=None):
    r = client.post("/calls", json={
        "direction": "inbound", "status": "in_progress",
        "patient_name": "Anitha Rao", "caller_phone": "+919000000001",
        "patient_id": patient_id,
    }, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _finish(client, headers, call_id, **extra):
    body = {"status": "completed", "duration_seconds": 90}
    body.update(extra)
    return client.patch(f"/calls/{call_id}", json=body, headers=headers)


# ------------------------------------------------------------ persisting

def test_the_language_a_caller_spoke_is_remembered_on_the_patient(clinic):
    client, _T, admin = clinic
    pid = _patient(client, admin)
    call_id = _call(client, admin, patient_id=pid)

    assert client.get(f"/patients/{pid}", headers=admin).json()["preferred_language"] is None

    r = _finish(client, admin, call_id, patient_id=pid, detected_language="te-IN")
    assert r.status_code == 200, r.text
    assert r.json()["detected_language"] == "te-IN"

    assert client.get(f"/patients/{pid}", headers=admin).json()["preferred_language"] == "te-IN"


def test_the_agent_can_read_it_back_on_lookup(clinic):
    """
    `GET /patients?search=` is what the agent's `patient_lookup` calls.
    The field has to be on that lean list shape, not just the detail one,
    or the agent never sees it.
    """
    client, _T, admin = clinic
    pid = _patient(client, admin)
    _finish(client, admin, _call(client, admin, pid), patient_id=pid, detected_language="hi-IN")

    matches = client.get("/patients", params={"search": "Anitha"}, headers=admin).json()
    assert matches, "search returned nothing"
    assert matches[0]["preferred_language"] == "hi-IN"


def test_a_later_call_in_another_language_updates_the_preference(clinic):
    """
    Last-spoken wins. A relative calling in English flips it, and the
    patient speaking Telugu again flips it back — a sticky first value
    could never recover from one wrong guess.
    """
    client, _T, admin = clinic
    pid = _patient(client, admin)

    _finish(client, admin, _call(client, admin, pid), patient_id=pid, detected_language="te-IN")
    _finish(client, admin, _call(client, admin, pid), patient_id=pid, detected_language="en-IN")

    assert client.get(f"/patients/{pid}", headers=admin).json()["preferred_language"] == "en-IN"


def test_a_silent_call_does_not_erase_a_known_language(clinic):
    """
    Nobody spoke, so there is no evidence — and overwriting a known
    language with a guess is worse than keeping the old one.
    """
    client, _T, admin = clinic
    pid = _patient(client, admin)
    _finish(client, admin, _call(client, admin, pid), patient_id=pid, detected_language="te-IN")

    silent = _call(client, admin, pid)
    _finish(client, admin, silent, patient_id=pid, status="no_answer")

    assert client.get(f"/patients/{pid}", headers=admin).json()["preferred_language"] == "te-IN"


def test_a_call_with_no_patient_stores_the_language_only_on_the_call(clinic):
    """An unidentified caller has no record to remember it against."""
    client, _T, admin = clinic
    call_id = _call(client, admin, patient_id=None)

    r = _finish(client, admin, call_id, detected_language="hi-IN")
    assert r.status_code == 200
    assert r.json()["detected_language"] == "hi-IN"


# ------------------------------------------------------------- isolation

def test_a_call_cannot_set_the_language_of_another_clinics_patient(ctx):
    client, _T = ctx
    a = _register(client, "Clinic A", "a@a.example.com")
    b = _register(client, "Clinic B", "b@b.example.com")

    theirs = _patient(client, a, name="Their Patient")
    call_id = _call(client, b, patient_id=None)

    r = _finish(client, b, call_id, patient_id=theirs, detected_language="hi-IN")
    # The router already 404s an out-of-org patient_id; the point is that
    # the language must not land on their record either way.
    assert r.status_code in (200, 404)
    assert client.get(f"/patients/{theirs}", headers=a).json()["preferred_language"] is None


# ------------------------------------------------------------ validation

def test_only_languages_the_voice_pipeline_speaks_can_be_stored():
    """
    The stored value is handed straight back to Sarvam TTS on the next
    call, so an unsupported tag would be spoken in the wrong voice or
    rejected mid-call.
    """
    for good in schemas.SPOKEN_LANGUAGES:
        assert schemas.CallLogUpdate(detected_language=good).detected_language == good

    for bad in ("fr-FR", "en", "english", "te", "hi-IN "):
        with pytest.raises(ValidationError):
            schemas.CallLogUpdate(detected_language=bad)


def test_the_field_is_optional_so_existing_finalize_calls_still_work():
    """The agent omits it entirely when the caller never spoke."""
    assert schemas.CallLogUpdate(status="completed").detected_language is None


def test_the_column_is_bounded_so_it_stays_indexable():
    """
    A language breakdown would want an index, and SQL Server cannot index
    VARCHAR(MAX). Guarded generally by
    tests/test_indexable_columns_are_bounded.py; pinned concretely here.
    """
    assert models.Patient.__table__.c.preferred_language.type.length == 8
    assert models.CallLog.__table__.c.detected_language.type.length == 8
