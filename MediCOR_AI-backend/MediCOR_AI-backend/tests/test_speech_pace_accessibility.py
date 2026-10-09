"""
Spoken accessibility accommodations (PF-16).

The agent was already instructed to speak clearly and slowly, but a caller
could not influence any of it. A patient who is hard of hearing, or
unfamiliar with the language, had no way to say so and no way to have it
remembered.

What is pinned here:

  * **It is set only because the patient asked.** Nothing infers it from
    age, call length, or how often someone asks for repetition. A system
    that guesses at disability guesses wrong in public.

  * **It is sticky, unlike the language preference.** Someone who needed
    it slower once is unlikely to need it faster later, so a later call
    that does not mention it must not quietly clear it.

  * **The value stays a closed vocabulary, never a pace number.** It is
    fed back into Sarvam TTS on the next call; an out-of-range figure
    would be rejected mid-call or produce something unintelligible.

  * **The model never sees it.** `PatientListOut` carries it so the agent
    can set up TTS on recognition, and the agent strips it before that
    dict reaches a live prompt — exactly as it already does for the
    language.

    pytest tests/test_speech_pace_accessibility.py -v
"""

import os
import sys
from datetime import datetime

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

from app import ehr_service, models, rate_limit, schemas  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import calls as calls_router  # noqa: E402
from app.routers import patients as patients_router  # noqa: E402


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
    app.include_router(calls_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        r = client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": "admin@north.example.com", "password": "Passw0rd!",
        })
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        yield client, Testing, admin


def _patient(client, admin, **extra):
    body = {"name": "Anitha Rao", "phone": "+919000000001"}
    body.update(extra)
    r = client.post("/patients", json=body, headers=admin)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _call(client, admin, patient_id, **extra):
    r = client.post("/calls", json={"direction": "inbound", "status": "in_progress"}, headers=admin)
    assert r.status_code in (200, 201), r.text
    call = r.json()
    body = {"status": "completed", "patient_id": patient_id}
    body.update(extra)
    r = client.patch(f"/calls/{call['id']}", json=body, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def _stored(Session, patient_id):
    db = Session()
    try:
        return db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    finally:
        db.close()


# --------------------------------------------------------------- storage

def test_asking_once_is_remembered_for_next_time(ctx):
    client, S, admin = ctx
    p = _patient(client, admin)
    assert _stored(S, p["id"]).speech_pace_preference is None

    _call(client, admin, p["id"], speech_pace_requested="slow")
    assert _stored(S, p["id"]).speech_pace_preference == "slow"


def test_a_call_where_nobody_asked_changes_nothing(ctx):
    client, S, admin = ctx
    p = _patient(client, admin)
    _call(client, admin, p["id"])
    assert _stored(S, p["id"]).speech_pace_preference is None


def test_the_preference_is_sticky_not_last_wins(ctx):
    """
    The opposite of `preferred_language`, deliberately.

    A later call that simply does not mention pace must not clear it:
    someone who needed it slower once is unlikely to need it faster later,
    and silently speeding back up would be the system forgetting something
    the patient had to ask for.
    """
    client, S, admin = ctx
    p = _patient(client, admin)
    _call(client, admin, p["id"], speech_pace_requested="slow")
    assert _stored(S, p["id"]).speech_pace_preference == "slow"

    _call(client, admin, p["id"])  # a later, ordinary call
    assert _stored(S, p["id"]).speech_pace_preference == "slow"


def test_the_call_keeps_its_own_evidence(ctx):
    """
    Per-call, so "when did they ask" is answerable later — the same
    relationship `detected_language` has to `preferred_language`.
    """
    client, _S, admin = ctx
    p = _patient(client, admin)
    call = _call(client, admin, p["id"], speech_pace_requested="slow")
    assert call["speech_pace_requested"] == "slow"


def test_an_unattributed_call_updates_nobody(ctx):
    """A call with no patient has nowhere to store a preference."""
    client, S, admin = ctx
    r = client.post("/calls", json={"direction": "inbound", "status": "in_progress"}, headers=admin)
    call = r.json()
    r = client.patch(
        f"/calls/{call['id']}",
        json={"status": "completed", "speech_pace_requested": "slow"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["speech_pace_requested"] == "slow"


# ------------------------------------------------------------ vocabulary

def test_the_vocabulary_is_closed():
    """
    A pace number would go straight back to Sarvam TTS on the next call.
    Out of range, that is either a mid-call rejection or an
    unintelligible voice — so the stored fact is what the patient asked
    for, and the figure lives in the agent's configuration.
    """
    assert schemas.SPEECH_PACE_PREFERENCES == ("slow",)
    with pytest.raises(ValidationError):
        schemas.CallLogUpdate(speech_pace_requested="0.62")
    with pytest.raises(ValidationError):
        schemas.CallLogUpdate(speech_pace_requested="fast")


def test_a_rejected_value_never_reaches_the_patient(ctx):
    client, S, admin = ctx
    p = _patient(client, admin)
    r = client.post("/calls", json={"direction": "inbound", "status": "in_progress"}, headers=admin)
    call = r.json()
    r = client.patch(
        f"/calls/{call['id']}",
        json={"status": "completed", "patient_id": p["id"], "speech_pace_requested": "very slow"},
        headers=admin,
    )
    assert r.status_code == 422, r.text
    assert _stored(S, p["id"]).speech_pace_preference is None


# ------------------------------------------------------- what the LLM sees

def test_the_agent_gets_it_but_the_model_does_not():
    """
    On the list shape so TTS can be set up the moment a returning caller
    is recognised; stripped by the agent before that dict is handed to a
    live prompt, exactly as the language already is.
    """
    assert "speech_pace_preference" in schemas.PatientListOut.model_fields
    assert "speech_pace_preference" in schemas.PatientDetailOut.model_fields

    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "voice-agent", "agent.py"),
        encoding="utf-8",
    ).read()
    assert 'match.pop("speech_pace_preference", None)' in src, (
        "the pace preference must be stripped before patient_lookup's result "
        "reaches the model, like preferred_language"
    )


def test_it_is_never_inferred_only_ever_asked_for():
    """
    Guards the rule the docstring opens with. If someone later derives
    this from age, call duration or repeat-request counts, that is a
    product decision that needs arguing for -- not something to arrive by
    way of a heuristic in a router.
    """
    src = open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "routers", "calls.py"),
        encoding="utf-8",
    ).read()
    block = src[src.index("speech_pace_requested"):]
    # The only thing that may set it is the value the agent reported.
    assert "patient.speech_pace_preference = call.speech_pace_requested" in block
    for smell in ("age", "duration_seconds", "repeat"):
        assert f"speech_pace_preference = {smell}" not in src
