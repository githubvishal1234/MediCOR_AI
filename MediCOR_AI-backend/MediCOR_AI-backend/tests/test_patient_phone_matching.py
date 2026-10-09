"""
Finding a patient by the number they rang from.

    pytest tests/test_patient_phone_matching.py -v

THE BUG THIS EXISTS TO PREVENT COMING BACK

`GET /patients?search=` matched the phone column with `ILIKE '%term%'`,
which compares two spellings of one number as strings. Twilio delivers a
caller in E.164 -- "+918688460308" -- while every real patient in this
deployment's database is stored the way a receptionist types it,
"8688460308". The substring never matched, so:

  * caller recognition (PF-02) matched nobody on any live call, and
  * `find_patient` reported the caller as unregistered, after which the
    agent did the reasonable thing and registered them AGAIN.

That second consequence is the expensive one. The production database
holds two "Krishna" rows with the same number and no date of birth,
created eighty minutes apart -- one patient, two medical records,
because the lookup could not see the first one. A duplicate patient
record is not a cosmetic problem: appointments, prescriptions and call
history attach to whichever row happened to be found.

So the tests below are less about search convenience than about not
splitting a person in half.

THE RULE

A phone-shaped term matches on its **last ten digits** against the
stored number with formatting stripped. Ten because that is the national
subscriber length in India and the US both, which makes every spelling
of one line -- with or without a country code, with or without
punctuation -- the same question. Terms with fewer than seven digits are
not treated as phone numbers at all, because a short fragment starts
matching unrelated people and matching the WRONG patient is far worse
than matching none.
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
from app.routers.patients import _phone_digits, _phone_suffix  # noqa: E402


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
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
        yield client, admin, Testing


def _add(client, headers, name, phone):
    r = client.post("/patients", json={"name": name, "phone": phone}, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _search(client, headers, term):
    r = client.get("/patients", params={"search": term}, headers=headers)
    assert r.status_code == 200, r.text
    return [p["name"] for p in r.json()]


# =====================================================================
# The normalisation itself
# =====================================================================


class TestPhoneNormalisation:
    def test_formatting_is_stripped(self):
        for written in ("+91 86884 60308", "+918688460308", "086884-60308",
                        "(868) 846-0308", "868.846.0308"):
            assert "8688460308" in _phone_digits(written)

    def test_a_phone_shaped_term_yields_its_last_ten_digits(self):
        assert _phone_suffix("+918688460308") == "8688460308"
        assert _phone_suffix("8688460308") == "8688460308"
        assert _phone_suffix("008918688460308") == "8688460308"
        assert _phone_suffix("+1 (555) 220-4471") == "5552204471"

    def test_a_short_term_is_shorter_than_ten_but_still_usable(self):
        assert _phone_suffix("8460308") == "8460308"

    def test_a_term_that_is_not_a_phone_number_is_left_alone(self):
        """
        Names, MRNs and short fragments must not be reinterpreted as
        phone suffixes -- that is how a search for "555" would start
        returning half the roster.
        """
        assert _phone_suffix("Krishna") is None
        assert _phone_suffix("86884") is None
        assert _phone_suffix("") is None
        assert _phone_suffix("MRN-54984-N") is None


# =====================================================================
# The bug, from both directions
# =====================================================================


class TestCallerIdFindsTheStoredPatient:
    def test_an_e164_caller_matches_a_nationally_stored_number(self, ctx):
        """
        THE regression test. Twilio hands us the country code; the
        receptionist did not type one. Before this fix the answer was
        zero patients, and the agent registered a duplicate.
        """
        client, admin, _ = ctx
        _add(client, admin, "Krishna", "8688460308")

        assert _search(client, admin, "+918688460308") == ["Krishna"]

    def test_a_nationally_typed_search_matches_an_e164_record(self, ctx):
        """The same mismatch the other way round, for a record the agent
        created from a Twilio caller id and a receptionist then looks up."""
        client, admin, _ = ctx
        _add(client, admin, "Vishal", "+919652359002")

        assert _search(client, admin, "9652359002") == ["Vishal"]

    def test_punctuation_in_the_stored_number_does_not_hide_it(self, ctx):
        """Imported records carry whatever formatting they arrived with."""
        client, admin, _ = ctx
        _add(client, admin, "Michael Chen", "(555) 220-4471")

        for term in ("+15552204471", "5552204471", "555-220-4471"):
            assert _search(client, admin, term) == ["Michael Chen"], term

    def test_every_spelling_of_one_line_finds_the_same_person(self, ctx):
        client, admin, _ = ctx
        _add(client, admin, "Anitha Rao", "8688460308")

        for term in ("+918688460308", "8688460308", "+91 86884 60308",
                     "008918688460308", "0091-86884-60308"):
            assert _search(client, admin, term) == ["Anitha Rao"], term


# =====================================================================
# What must NOT start matching
# =====================================================================


class TestItDoesNotOverMatch:
    def test_a_different_number_is_still_a_different_patient(self, ctx):
        client, admin, _ = ctx
        _add(client, admin, "Krishna", "8688460308")
        _add(client, admin, "Vishal", "9652359002")

        assert _search(client, admin, "+918688460308") == ["Krishna"]
        assert _search(client, admin, "+919652359002") == ["Vishal"]

    def test_a_short_fragment_does_not_become_a_suffix_match(self, ctx):
        """
        "86884" is five digits. Treated as a phone suffix it would match
        every number ending in it; it stays an ordinary substring search,
        which is what a receptionist typing a partial number expects.
        """
        client, admin, _ = ctx
        _add(client, admin, "Krishna", "8688460308")
        _add(client, admin, "Other Person", "9999986884")

        assert set(_search(client, admin, "86884")) == {"Krishna", "Other Person"}

    def test_name_and_mrn_search_are_unchanged(self, ctx):
        client, admin, _ = ctx
        created = _add(client, admin, "Krishna", "8688460308")
        _add(client, admin, "Vishal", "9652359002")

        assert _search(client, admin, "Krishna") == ["Krishna"]
        assert _search(client, admin, created["mrn"]) == ["Krishna"]

    def test_two_patients_on_one_handset_both_come_back(self, ctx):
        """
        A shared family phone. Returning both is correct and is what
        makes the agent's "exactly one match" rule protective -- it
        declines to personalise rather than guessing which relative is
        calling.
        """
        client, admin, _ = ctx
        _add(client, admin, "Krishna", "8688460308")
        _add(client, admin, "Krishna's Mother", "+918688460308")

        assert len(_search(client, admin, "+918688460308")) == 2


# =====================================================================
# Tenancy
# =====================================================================


class TestPhoneSearchStaysInsideTheOrg:
    def test_another_clinics_patient_is_not_reachable_by_phone(self, ctx):
        """
        The one confirmed cross-org IDOR in this codebase's history came
        from a query that forgot an org filter. Widening a search is
        exactly the change that could reintroduce it, so the boundary is
        asserted rather than assumed.
        """
        client, admin, _ = ctx
        _add(client, admin, "Krishna", "8688460308")

        r = client.post("/auth/register", json={
            "org_name": "Other Clinic", "full_name": "Other Admin",
            "email": "admin@other.example.com", "password": "Passw0rd!",
        })
        other = {"Authorization": f"Bearer {r.json()['access_token']}"}

        assert _search(client, other, "+918688460308") == []
        assert _search(client, other, "8688460308") == []
        assert _search(client, admin, "+918688460308") == ["Krishna"]
