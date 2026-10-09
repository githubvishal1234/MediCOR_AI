"""
The public plan catalogue (`GET /public/plans`).

This endpoint exists because the landing page used to assert prices of
its own, and every figure had drifted from the catalogue a super admin
actually maintains and bills against. So the tests that matter are less
about the happy path than about what must never come back out of it:
a plan nobody can buy, a field nobody meant to publish, or anything at
all belonging to a clinic.

    pytest tests/test_public_plan_catalog.py -v
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
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()

    def override():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override
    yield session
    app.dependency_overrides.clear()
    session.close()


@pytest.fixture()
def client(db):
    return TestClient(app)


def add_plan(db, name, cents, **kw):
    plan = models.Plan(
        name=name,
        description=kw.pop("description", f"{name} plan"),
        monthly_price_cents=cents,
        voice_minutes_limit=kw.pop("voice_minutes_limit", 1000),
        user_limit=kw.pop("user_limit", 5),
        patient_limit=kw.pop("patient_limit", 250),
        ehr_access=kw.pop("ehr_access", False),
        knowledge_base_access=kw.pop("knowledge_base_access", True),
        features_csv=kw.pop("features_csv", "voice_agent"),
        is_active=kw.pop("is_active", True),
    )
    db.add(plan)
    db.commit()
    return plan


class TestWhatIsPublished:
    def test_it_needs_no_credential(self, client, db):
        """
        The whole point: a visitor who has never signed in must be able to
        read the prices. No header, no token, no API key.
        """
        add_plan(db, "Starter", 9900)
        r = client.get("/public/plans")
        assert r.status_code == 200
        assert [p["name"] for p in r.json()] == ["Starter"]

    def test_plans_come_back_cheapest_first(self, client, db):
        """
        Ordered here rather than in the page, so two clients cannot
        disagree about what "first" means.
        """
        add_plan(db, "Enterprise", 79900)
        add_plan(db, "Starter", 9900)
        add_plan(db, "Growth", 29900)
        names = [p["name"] for p in client.get("/public/plans").json()]
        assert names == ["Starter", "Growth", "Enterprise"]

    def test_the_real_figures_are_served_not_invented(self, client, db):
        """
        The defect this endpoint closes. The page previously said $199 for
        a plan the platform sells at $99, with 500 minutes against a real
        1,000 -- a quote no super admin had set and no clinic would be
        billed.
        """
        add_plan(db, "Starter", 9900, voice_minutes_limit=1000, user_limit=5)
        plan = client.get("/public/plans").json()[0]
        assert plan["monthly_price_cents"] == 9900
        assert plan["voice_minutes_limit"] == 1000
        assert plan["user_limit"] == 5

    def test_an_empty_catalogue_is_an_empty_list_not_an_error(self, client, db):
        """A platform that sells nothing yet is not a 500."""
        r = client.get("/public/plans")
        assert r.status_code == 200
        assert r.json() == []

    def test_unlimited_limits_survive_as_null(self, client, db):
        """
        `user_limit` / `patient_limit` are nullable meaning unlimited.
        Coercing them to 0 would advertise a plan that permits nothing.
        """
        add_plan(db, "Enterprise", 79900, user_limit=None, patient_limit=None)
        plan = client.get("/public/plans").json()[0]
        assert plan["user_limit"] is None
        assert plan["patient_limit"] is None


class TestWhatIsNeverPublished:
    def test_a_deactivated_plan_is_not_advertised(self, client, db):
        """
        Deactivating a plan is the platform deciding to stop selling it.
        Listing it anyway advertises something nobody can buy.
        """
        add_plan(db, "Starter", 9900, is_active=True)
        add_plan(db, "Retired", 4900, is_active=False)
        names = [p["name"] for p in client.get("/public/plans").json()]
        assert names == ["Starter"]
        assert "Retired" not in names

    def test_the_response_is_an_explicit_field_list(self, client, db):
        """
        Pinned as a set, not a subset check. `PublicPlanOut` is served to
        anyone on the internet, so a new `Plan` column must not start
        being published the day somebody adds it -- the same guard, and
        the same reasoning, as `LinkAppointmentOut`.

        If this fails because a field was added deliberately, widen it
        here and say why. That is the point of the failure.
        """
        add_plan(db, "Starter", 9900)
        assert set(client.get("/public/plans").json()[0]) == {
            "name",
            "description",
            "monthly_price_cents",
            # Added for HV-5, deliberately. A price without its currency
            # is not a price: the pricing page and the in-app checkout
            # both rendered a "$" while the platform bills in rupees.
            # It exposes nothing about any clinic -- it is one
            # deployment-wide setting.
            "currency",
            "voice_minutes_limit",
            "user_limit",
            "patient_limit",
            "ehr_access",
            "knowledge_base_access",
        }

    def test_internal_fields_stay_internal(self, client, db):
        """
        `id` is omitted because nothing public addresses a plan -- there
        is no self-serve checkout and `POST /billing/upgrade` is 501 by
        design. `features_csv` holds internal keys, and the timestamps
        are platform bookkeeping.
        """
        add_plan(db, "Starter", 9900, features_csv="voice_agent,knowledge_base")
        plan = client.get("/public/plans").json()[0]
        for leaked in ("id", "features", "features_csv", "is_active",
                       "created_at", "updated_at", "subscribed_org_count"):
            assert leaked not in plan

    def test_nothing_about_any_clinic_leaks(self, client, db):
        """
        The one thing that would make this endpoint dangerous. It reads
        platform configuration; a subscription is a clinic's business
        relationship and no count, name or id of one may appear here.
        """
        org = models.Organization(name="Some Clinic")
        db.add(org)
        db.commit()
        plan = add_plan(db, "Starter", 9900)
        db.add(models.Subscription(org_id=org.id, plan_id=plan.id, status="active"))
        db.commit()

        body = client.get("/public/plans").text
        assert "Some Clinic" not in body
        assert org.id not in body
