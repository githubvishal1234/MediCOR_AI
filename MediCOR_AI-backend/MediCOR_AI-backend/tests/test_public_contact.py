"""
The public contact form (`GET`/`POST /public/contact`) and its inbox.

The footer's "Contact Sales" was `href="#contact"` -- an anchor to a
section that did not exist, so it scrolled nowhere. This is the endpoint
that replaced it, and it is the **fourth unauthenticated route** in the
application, so most of what is worth testing is what it refuses.

One test here is the load-bearing one:
`test_an_enquiry_that_is_written_can_actually_be_read`. A public write
whose rows nobody reads is the `webhooks` defect -- a working control
over a capability that does not exist -- and that is precisely what a
contact form silently becomes if the console half is ever removed.

    pytest tests/test_public_contact.py -v
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

from app import models, rate_limit, security  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

GOOD = {
    "name": "Dr Anita Rao",
    "email": "anita@sunrise-clinic.example",
    "phone": "+91 80 7158 3192",
    "clinic_name": "Sunrise Family Practice",
    "message": "We take about 200 calls a week and miss most of them after 6pm.",
}


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Module-level and in-process, so one test's posts trip the next's."""
    rate_limit._attempts.clear()
    yield
    rate_limit._attempts.clear()


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


@pytest.fixture()
def admin_headers(db):
    admin = models.SuperAdmin(
        email="root@platform.example",
        full_name="Platform Root",
        hashed_password=security.hash_password("irrelevant"),
    )
    db.add(admin)
    db.commit()
    return {"Authorization": f"Bearer {security.create_super_admin_token(admin.id)}"}


# ---------------------------------------------------------------------
# Sending one
# ---------------------------------------------------------------------


class TestSubmitting:
    def test_a_stranger_can_send_one(self, client, db):
        r = client.post("/public/contact", json=GOOD)
        assert r.status_code == 200
        assert r.json() == {"received": True}

        row = db.query(models.ContactEnquiry).one()
        assert row.name == "Dr Anita Rao"
        assert row.clinic_name == "Sunrise Family Practice"
        assert row.status == "new"

    def test_the_acknowledgement_promises_nothing(self, client, db):
        """
        No id, no reference number, no response time. An id would invite a
        lookup endpoint over other people's correspondence, and a response
        time is a promise this application cannot keep -- the same rule the
        missed-call message follows when it declines to say anyone will
        ring back.
        """
        body = client.post("/public/contact", json=GOOD).json()
        assert set(body) == {"received"}

    def test_only_name_email_and_message_are_required(self, client, db):
        r = client.post(
            "/public/contact",
            json={"name": "A", "email": "a@b.example", "message": "hello"},
        )
        assert r.status_code == 200
        row = db.query(models.ContactEnquiry).one()
        assert row.phone is None and row.clinic_name is None

    @pytest.mark.parametrize(
        "payload",
        [
            {**GOOD, "email": "not-an-address"},
            {**GOOD, "name": ""},
            {**GOOD, "message": ""},
            {**GOOD, "message": "x" * 4001},
            {**GOOD, "name": "x" * 161},
        ],
    )
    def test_bad_input_is_refused(self, client, db, payload):
        """
        Bounds are the only thing between this table and whatever somebody
        pastes, because nothing here is behind a login.
        """
        assert client.post("/public/contact", json=payload).status_code == 422
        assert db.query(models.ContactEnquiry).count() == 0

    def test_it_is_rate_limited_per_ip(self, client, db, monkeypatch):
        monkeypatch.setattr(settings, "contact_rate_limit_attempts", 3)
        for _ in range(3):
            assert client.post("/public/contact", json=GOOD).status_code == 200
        assert client.post("/public/contact", json=GOOD).status_code == 429
        assert db.query(models.ContactEnquiry).count() == 3

    def test_a_failure_is_not_dressed_up_as_success(self, client, db, monkeypatch):
        """
        Deliberately unlike the inbound Twilio webhook, which answers 200
        to everything because Twilio retries a non-2xx and a retry storm
        against an endpoint that cancels appointments was the worse
        outcome. Here the caller is a browser with a person watching it,
        and telling them it sent when it did not is how an enquiry is lost
        silently -- so the 429 above is a real 429.
        """
        monkeypatch.setattr(settings, "contact_rate_limit_attempts", 1)
        client.post("/public/contact", json=GOOD)
        blocked = client.post("/public/contact", json=GOOD)
        assert blocked.status_code != 200

    def test_the_message_is_stored_verbatim_and_never_parsed(self, client, db):
        """
        Same rule as the inbound-message webhook: free text from a stranger
        is data, never an instruction. Nothing looks for keywords in it.
        """
        text = "CANCEL my appointment; STOP; <script>alert(1)</script>"
        client.post("/public/contact", json={**GOOD, "message": text})
        assert db.query(models.ContactEnquiry).one().message == text


# ---------------------------------------------------------------------
# Reading them
# ---------------------------------------------------------------------


class TestTheInbox:
    def test_an_enquiry_that_is_written_can_actually_be_read(
        self, client, db, admin_headers
    ):
        """
        **The load-bearing test.** A public form whose rows nobody reads is
        a working control over a capability that does not exist. If the
        console half is ever removed, this fails rather than the enquiries
        quietly piling up unseen.
        """
        client.post("/public/contact", json=GOOD)
        rows = client.get("/admin/support/enquiries", headers=admin_headers).json()
        assert [r["email"] for r in rows] == ["anita@sunrise-clinic.example"]

    def test_the_inbox_needs_a_super_admin(self, client, db):
        assert client.get("/admin/support/enquiries").status_code in (401, 403)

    def test_the_source_ip_is_stored_but_never_served(self, client, db, admin_headers):
        """
        Kept for abuse triage only -- the endpoint is public, and without
        it a flood cannot be told from a busy week. Putting a stranger's
        address on a screen somebody reads daily would make it ordinary
        operational data instead of the narrow thing it was collected for.
        """
        client.post("/public/contact", json=GOOD)
        assert db.query(models.ContactEnquiry).one().source_ip
        row = client.get("/admin/support/enquiries", headers=admin_headers).json()[0]
        assert "source_ip" not in row

    def test_marking_handled_records_who_and_when(self, client, db, admin_headers):
        client.post("/public/contact", json=GOOD)
        eid = db.query(models.ContactEnquiry).one().id

        r = client.patch(
            f"/admin/support/enquiries/{eid}",
            json={"status": "handled"},
            headers=admin_headers,
        )
        assert r.status_code == 200 and r.json()["status"] == "handled"

        db.expire_all()
        row = db.query(models.ContactEnquiry).one()
        assert row.handled_at is not None and row.handled_by is not None

    def test_reopening_clears_the_handled_stamp(self, client, db, admin_headers):
        """
        Or the pair describes a state the row is no longer in -- a row
        marked "new" that still says who closed it and when.
        """
        client.post("/public/contact", json=GOOD)
        eid = db.query(models.ContactEnquiry).one().id
        for status in ("handled", "new"):
            client.patch(
                f"/admin/support/enquiries/{eid}",
                json={"status": status},
                headers=admin_headers,
            )
        db.expire_all()
        row = db.query(models.ContactEnquiry).one()
        assert row.handled_at is None and row.handled_by is None

    def test_only_two_statuses_are_accepted(self, client, db, admin_headers):
        """
        An inbox, not a workflow. A third status nobody advances past is
        the `callback_status` mistake: a column staff are meant to work
        that quietly fills up instead.
        """
        client.post("/public/contact", json=GOOD)
        eid = db.query(models.ContactEnquiry).one().id
        r = client.patch(
            f"/admin/support/enquiries/{eid}",
            json={"status": "In Progress"},
            headers=admin_headers,
        )
        assert r.status_code == 422

    def test_a_missing_enquiry_is_a_404(self, client, db, admin_headers):
        r = client.patch(
            "/admin/support/enquiries/nope",
            json={"status": "handled"},
            headers=admin_headers,
        )
        assert r.status_code == 404


class TestItBelongsToNoClinic:
    def test_the_table_has_no_org_id(self):
        """
        The only table in this schema without one, and deliberately.
        Whoever filled the form is not a customer, which is the whole
        reason they are writing; attaching an org would mean guessing
        which clinic an anonymous enquiry belongs to, and this codebase
        refuses that guess everywhere else.
        """
        assert "org_id" not in models.ContactEnquiry.__table__.columns

    def test_the_response_carries_no_tenant_field(self, client, db, admin_headers):
        client.post("/public/contact", json=GOOD)
        row = client.get("/admin/support/enquiries", headers=admin_headers).json()[0]
        assert "org_id" not in row and "org_name" not in row


# ---------------------------------------------------------------------
# The number, which is configuration and never invented
# ---------------------------------------------------------------------


class TestContactDetails:
    def test_nothing_configured_returns_nulls_not_placeholders(self, client, monkeypatch):
        """
        Blank is the default and the normal state. A placeholder number on
        a public page is not a smaller problem than no number: somebody
        dials it.
        """
        monkeypatch.setattr(settings, "support_phone", "")
        monkeypatch.setattr(settings, "support_email", "")
        monkeypatch.setattr(settings, "support_hours", "")
        assert client.get("/public/contact").json() == {
            "phone": None,
            "email": None,
            "hours": None,
        }

    def test_configured_details_are_served(self, client, monkeypatch):
        monkeypatch.setattr(settings, "support_phone", "+91 80 4567 8900")
        monkeypatch.setattr(settings, "support_email", "hello@example.com")
        monkeypatch.setattr(settings, "support_hours", "Mon-Fri, 9am-6pm IST")
        body = client.get("/public/contact").json()
        assert body["phone"] == "+91 80 4567 8900"
        assert body["email"] == "hello@example.com"
        assert body["hours"] == "Mon-Fri, 9am-6pm IST"

    def test_whitespace_only_counts_as_unset(self, client, monkeypatch):
        """`SUPPORT_PHONE=" "` in a .env must not render an empty button."""
        monkeypatch.setattr(settings, "support_phone", "   ")
        assert client.get("/public/contact").json()["phone"] is None

    def test_it_needs_no_credential(self, client):
        assert client.get("/public/contact").status_code == 200
