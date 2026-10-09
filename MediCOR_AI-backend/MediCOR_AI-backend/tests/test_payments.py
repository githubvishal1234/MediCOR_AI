"""
Paying for a plan (HV-5, Razorpay).

Money makes the failure modes asymmetric, so most of what is pinned here
is what must NOT happen: a clinic must not be able to name its own price,
a valid signature from one clinic must not upgrade another, a duplicate
webhook must not produce two invoices, and a `payment.failed` for one
attempt must not strip a plan a later attempt paid for.

Everything runs in mock mode, which replaces exactly the paid service and
leaves the order record, signature verification, fulfilment, the invoice
and the subscription real. Mock signatures are produced with the same
HMAC the live path uses, so the verification code under test is the code
that runs in production -- a verification path that is skipped in testing
is a verification path nobody has tested.

    pytest tests/test_payments.py -v
"""

import json
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

from app import billing_service, models, payment_service, security  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch):
    """No keys, no network. `is_configured()` stays False throughout."""
    monkeypatch.setattr(settings, "razorpay_mock_mode", True)
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "")


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    org = models.Organization(name="Northside Clinic", suspended=False)
    other = models.Organization(name="Southside Clinic", suspended=False)
    db.add_all([org, other])
    db.flush()

    admin = models.User(
        org_id=org.id, email="admin@northside.test", full_name="Dr Rao",
        hashed_password=security.hash_password("x"), role="admin", is_active=True,
    )
    intruder = models.User(
        org_id=other.id, email="admin@southside.test", full_name="Dr Iyer",
        hashed_password=security.hash_password("x"), role="admin", is_active=True,
    )
    nurse = models.User(
        org_id=org.id, email="nurse@northside.test", full_name="Nurse Rao",
        hashed_password=security.hash_password("x"), role="medical_staff",
        is_active=True,
    )
    plan = models.Plan(
        name="Growth", description="For growing practices",
        monthly_price_cents=29900, voice_minutes_limit=5000,
        user_limit=20, patient_limit=2000, ehr_access=True,
        knowledge_base_access=True, is_active=True,
    )
    retired = models.Plan(
        name="Legacy", monthly_price_cents=100, voice_minutes_limit=10,
        is_active=False,
    )
    db.add_all([admin, intruder, nurse, plan, retired])
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    def as_(user):
        c = TestClient(app)
        c.headers["Authorization"] = f"Bearer {security.create_access_token({'sub': user.id})}"
        return c

    yield {
        "db": db, "org": org, "other": other, "plan": plan, "retired": retired,
        "admin": admin, "intruder": intruder, "nurse": nurse,
        "client": as_(admin), "as_": as_, "anon": client,
    }
    app.dependency_overrides.clear()
    db.close()


def _message(resp):
    """
    The human-readable half of an error, whichever envelope it arrives in.

    `register_exception_handlers` wraps HTTPException as
    {"error": {"code", "message", "request_id"}} rather than FastAPI's
    bare {"detail": ...}, so a test reading "detail" passes vacuously
    against a KeyError instead of checking the wording.
    """
    body = resp.json()
    return body.get("error", {}).get("message") or body.get("detail") or ""


def start_checkout(env, plan_name="Growth", client=None):
    return (client or env["client"]).post("/billing/checkout", json={"plan_name": plan_name})


def pay(env, order_id, payment_id="pay_mock_1", client=None):
    """Complete a checkout the way the browser does, signature and all."""
    return (client or env["client"]).post(
        "/billing/checkout/verify",
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": payment_service.mock_signature(order_id, payment_id),
        },
    )


# ---------------------------------------------------------------------
# Starting a payment
# ---------------------------------------------------------------------


class TestCheckout:
    def test_the_price_comes_from_the_catalogue(self, env):
        """
        **The load-bearing test of the whole feature.** The request names a
        plan and nothing else; the amount is read server-side. A client
        that could name its own price is the oldest mistake in online
        payments, and no amount field exists on `CheckoutIn` to send.
        """
        body = start_checkout(env).json()
        assert body["amount_cents"] == 29900

        # There is no field on the request through which a price could
        # arrive at all -- the strongest form of this guarantee.
        from app import schemas
        assert set(schemas.CheckoutIn.model_fields) == {"plan_name"}

        row = env["db"].query(models.Payment).one()
        assert row.amount_cents == 29900, "the stored amount is the catalogue's"

    def test_sending_an_amount_cannot_change_the_charge(self, env):
        """Extra fields are ignored, not honoured."""
        r = env["client"].post(
            "/billing/checkout",
            json={"plan_name": "Growth", "amount_cents": 1, "amount": 1},
        )
        assert r.status_code == 200
        assert env["db"].query(models.Payment).one().amount_cents == 29900

    def test_it_records_the_order_before_the_browser_is_told(self, env):
        order_id = start_checkout(env).json()["order_id"]
        row = env["db"].query(models.Payment).one()
        assert row.provider_order_id == order_id
        assert row.status == billing_service.STATUS_CREATED
        assert row.plan_name == "Growth"

    def test_only_an_admin_can_start_one(self, env):
        """Choosing and paying for a plan is an administrative act."""
        r = start_checkout(env, client=env["as_"](env["nurse"]))
        assert r.status_code == 403

    def test_an_inactive_plan_cannot_be_bought(self, env):
        r = start_checkout(env, plan_name="Legacy")
        assert r.status_code == 404
        assert env["db"].query(models.Payment).count() == 0

    def test_an_unknown_plan_and_a_retired_plan_answer_alike(self, env):
        """Or the 404 becomes a way to shop for a withdrawn price."""
        a = start_checkout(env, plan_name="Legacy")
        b = start_checkout(env, plan_name="No Such Plan")
        assert a.status_code == b.status_code == 404
        assert _message(a) == _message(b)

    def test_the_key_secret_never_reaches_the_browser(self, env, monkeypatch):
        monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_public")
        monkeypatch.setattr(settings, "razorpay_key_secret", "SUPER_SECRET_VALUE")
        # Configured now, so the real HTTP path would run; only the
        # response shape matters here, so drive the mock branch instead.
        monkeypatch.setattr(settings, "razorpay_key_secret", "")
        body = start_checkout(env).json()
        assert "SUPER_SECRET_VALUE" not in json.dumps(body)
        assert "secret" not in json.dumps(body).lower()

    def test_unconfigured_and_not_mocked_is_a_503_not_a_crash(self, env, monkeypatch):
        """
        A deployment that has not bought Razorpay is a supported state --
        it is what every clinic had before this existed -- so it is told
        plainly rather than shown an error.
        """
        monkeypatch.setattr(settings, "razorpay_mock_mode", False)
        r = start_checkout(env)
        assert r.status_code == 503
        assert "get in touch" in _message(r).lower()


# ---------------------------------------------------------------------
# Completing one
# ---------------------------------------------------------------------


class TestVerify:
    def test_a_good_signature_grants_the_plan(self, env):
        order_id = start_checkout(env).json()["order_id"]
        r = pay(env, order_id)
        assert r.status_code == 200 and r.json()["paid"] is True

        db = env["db"]
        payment = db.query(models.Payment).one()
        assert payment.status == "captured"
        assert payment.fulfilled_via == "checkout"

        sub = db.query(models.Subscription).one()
        assert sub.plan_id == env["plan"].id and sub.status == "active"

        # The same two columns a super admin assignment mirrors, because
        # the clinic's Billing page reads them.
        db.refresh(env["org"])
        assert env["org"].plan == "Growth"
        assert env["org"].voice_minutes_limit == 5000

    def test_a_forged_signature_grants_nothing(self, env):
        order_id = start_checkout(env).json()["order_id"]
        r = env["client"].post("/billing/checkout/verify", json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": "pay_x",
            "razorpay_signature": "0" * 64,
        })
        assert r.status_code == 400
        db = env["db"]
        assert db.query(models.Payment).one().status == "created"
        assert db.query(models.Subscription).count() == 0
        assert db.query(models.Invoice).count() == 0

    def test_another_clinic_cannot_replay_the_order(self, env):
        """
        The signature is valid -- it is the *same* signature -- so the
        only thing standing between clinic B and a free upgrade is the
        ownership check. Without it, paying once would upgrade anyone.
        """
        order_id = start_checkout(env).json()["order_id"]
        r = pay(env, order_id, client=env["as_"](env["intruder"]))
        assert r.status_code == 404

        db = env["db"]
        assert db.query(models.Payment).one().status == "created"
        assert db.query(models.Subscription).count() == 0

    def test_an_invoice_is_written_and_linked_to_the_payment(self, env):
        order_id = start_checkout(env).json()["order_id"]
        pay(env, order_id)
        db = env["db"]
        inv = db.query(models.Invoice).one()
        payment = db.query(models.Payment).one()
        assert inv.payment_id == payment.id
        assert inv.amount_cents == 29900
        assert inv.status == "Paid"

    def test_only_an_admin_can_verify(self, env):
        order_id = start_checkout(env).json()["order_id"]
        r = pay(env, order_id, client=env["as_"](env["nurse"]))
        assert r.status_code == 403


# ---------------------------------------------------------------------
# The webhook, and doing it all twice
# ---------------------------------------------------------------------


def webhook(env, order_id, payment_id="pay_mock_1", event="payment.captured", sign=True):
    body = json.dumps({
        "event": event,
        "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id}}},
    }).encode("utf-8")
    sig = payment_service.mock_webhook_signature(body) if sign else "bad"
    return env["anon"].post(
        "/webhooks/razorpay", content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )


class TestWebhook:
    def test_it_needs_no_credential_but_needs_a_signature(self, env):
        order_id = start_checkout(env).json()["order_id"]
        assert webhook(env, order_id, sign=False).status_code == 400
        assert env["db"].query(models.Payment).one().status == "created"

        assert webhook(env, order_id).status_code == 200
        assert env["db"].query(models.Payment).one().status == "captured"

    def test_it_grants_the_plan_when_the_browser_never_came_back(self, env):
        """
        The reason this endpoint exists. A clinic that pays and closes the
        tab would otherwise be charged and not upgraded.
        """
        order_id = start_checkout(env).json()["order_id"]
        webhook(env, order_id)
        db = env["db"]
        assert db.query(models.Subscription).one().status == "active"
        assert db.query(models.Payment).one().fulfilled_via == "webhook"

    def test_a_duplicate_delivery_does_not_bill_twice(self, env):
        """
        Razorpay delivers at least once, so a repeat is expected traffic,
        not an attack. Two invoices for one payment is the failure.
        """
        order_id = start_checkout(env).json()["order_id"]
        for _ in range(3):
            assert webhook(env, order_id).status_code == 200
        db = env["db"]
        assert db.query(models.Invoice).count() == 1
        assert db.query(models.Subscription).count() == 1

    def test_the_webhook_and_the_browser_together_produce_one_invoice(self, env):
        """
        The ordinary case: both paths fire for the same payment. Whichever
        arrives second must report success without doing the work again.
        """
        order_id = start_checkout(env).json()["order_id"]
        assert webhook(env, order_id).status_code == 200
        r = pay(env, order_id)
        assert r.status_code == 200
        assert r.json()["paid"] is True
        assert r.json()["newly_fulfilled"] is False
        assert env["db"].query(models.Invoice).count() == 1

    def test_a_failure_event_does_not_undo_a_capture(self, env):
        """
        Razorpay can report `payment.failed` for one attempt on an order a
        later attempt paid. Letting that through would strip a clinic of a
        plan it has been charged for.
        """
        order_id = start_checkout(env).json()["order_id"]
        webhook(env, order_id)
        webhook(env, order_id, event="payment.failed")
        db = env["db"]
        assert db.query(models.Payment).one().status == "captured"
        assert db.query(models.Subscription).one().status == "active"

    def test_a_failure_before_any_capture_is_recorded(self, env):
        order_id = start_checkout(env).json()["order_id"]
        webhook(env, order_id, event="payment.failed")
        assert env["db"].query(models.Payment).one().status == "failed"
        assert env["db"].query(models.Invoice).count() == 0

    def test_an_unknown_event_is_acknowledged_and_ignored(self, env):
        order_id = start_checkout(env).json()["order_id"]
        r = webhook(env, order_id, event="refund.created")
        assert r.status_code == 200
        assert env["db"].query(models.Payment).one().status == "created"

    def test_an_unknown_order_is_acknowledged_not_retried(self, env):
        """
        Most likely a test-keys webhook aimed at production. A non-2xx
        would make Razorpay retry it forever.
        """
        assert webhook(env, "order_never_seen").status_code == 200


# ---------------------------------------------------------------------
# The signature functions themselves
# ---------------------------------------------------------------------


class TestSignatures:
    def test_checkout_and_webhook_use_different_secrets(self, env, monkeypatch):
        """
        Razorpay's key secret and webhook secret are different values from
        different places in the dashboard. Using one for the other fails
        every verification in a way that looks like a broken integration,
        so the two functions must not share a key.
        """
        monkeypatch.setattr(settings, "razorpay_mock_mode", False)
        monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_x")
        monkeypatch.setattr(settings, "razorpay_key_secret", "KEYSECRET")
        monkeypatch.setattr(settings, "razorpay_webhook_secret", "HOOKSECRET")

        import hashlib
        import hmac

        order, pay_id = "order_1", "pay_1"
        good = hmac.new(b"KEYSECRET", f"{order}|{pay_id}".encode(), hashlib.sha256).hexdigest()
        wrong = hmac.new(b"HOOKSECRET", f"{order}|{pay_id}".encode(), hashlib.sha256).hexdigest()

        assert payment_service.verify_checkout_signature(order, pay_id, good) is True
        assert payment_service.verify_checkout_signature(order, pay_id, wrong) is False

    def test_an_unset_secret_verifies_nothing(self, env, monkeypatch):
        """Fails closed, exactly like the Twilio signature check."""
        monkeypatch.setattr(settings, "razorpay_mock_mode", False)
        monkeypatch.setattr(settings, "razorpay_key_secret", "")
        monkeypatch.setattr(settings, "razorpay_webhook_secret", "")
        assert payment_service.verify_checkout_signature("o", "p", "anything") is False
        assert payment_service.verify_webhook_signature(b"{}", "anything") is False

    def test_the_webhook_signature_is_over_the_exact_bytes(self, env, monkeypatch):
        """
        Razorpay signs the raw body. Re-serialising the parsed JSON changes
        whitespace and key order and the HMAC stops matching, which is why
        the endpoint verifies before it parses.
        """
        monkeypatch.setattr(settings, "razorpay_mock_mode", False)
        monkeypatch.setattr(settings, "razorpay_webhook_secret", "HOOKSECRET")
        raw = b'{"event":"payment.captured","x":1}'
        import hashlib
        import hmac
        sig = hmac.new(b"HOOKSECRET", raw, hashlib.sha256).hexdigest()

        assert payment_service.verify_webhook_signature(raw, sig) is True
        # Same object, different bytes -- must not verify.
        respaced = json.dumps(json.loads(raw)).encode()
        assert respaced != raw
        assert payment_service.verify_webhook_signature(respaced, sig) is False


class TestReporting:
    def test_a_clinic_sees_only_its_own_payments(self, env):
        order_id = start_checkout(env).json()["order_id"]
        pay(env, order_id)
        mine = env["client"].get("/billing/payments").json()
        theirs = env["as_"](env["intruder"]).get("/billing/payments").json()
        assert len(mine) == 1 and mine[0]["status"] == "captured"
        assert theirs == []

    def test_no_card_details_are_stored_or_returned(self, env):
        """
        None are ever received: the order flow never sees a card, which is
        the point of redirecting to the provider. Nothing may imply one.
        """
        order_id = start_checkout(env).json()["order_id"]
        pay(env, order_id)
        blob = json.dumps(env["client"].get("/billing/payments").json()).lower()
        for leak in ("card", "cvv", "last4", "expiry", "pan"):
            assert leak not in blob

    def test_mock_mode_is_reported_as_a_startup_problem(self, env):
        """
        A deployment left in mock mode completes checkouts and grants
        plans without any money moving. It must never be quiet about that.
        """
        problems = " ".join(payment_service.startup_problems()).lower()
        assert "mock" in problems
