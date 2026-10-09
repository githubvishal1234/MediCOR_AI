"""
Self-service password reset (GAP-U1).

Before this there was no path at all: a locked-out staff member needed an
administrator or a direct database edit.

Most of these tests are about two properties, both of which are easy to
lose in a later refactor:

  * **The response never reveals whether an address is registered.** A
    different status, a different message, or even a noticeably different
    response time turns this endpoint into a way to enumerate a clinic's
    staff. Same reasoning as the single error `login` and `accept-invite`
    return.

  * **A token is single-use and short-lived.** It is a bearer credential
    for an existing account -- a stronger thing than an invitation, and it
    is cleared on use so a spent link is indistinguishable from one that
    never existed.

    pytest tests/test_password_reset.py -v
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

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models, notification_service, rate_limit  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.security import generate_password_reset_token, verify_password  # noqa: E402

EMAIL = "admin@north.example.com"
OLD_PASSWORD = "Passw0rd!"
NEW_PASSWORD = "N3wPassw0rd!"


@pytest.fixture()
def ctx(monkeypatch):
    rate_limit._attempts.clear()
    # Generous by default so the behavioural tests are not fighting the
    # limiter; the limiter has its own test below.
    monkeypatch.setattr(settings, "password_reset_rate_limit_attempts", 100)

    sent = []
    monkeypatch.setattr(notification_service, "_email_configured", lambda: True)
    monkeypatch.setattr(
        notification_service, "_send_email",
        lambda to, subject, body: sent.append((to, subject, body)),
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        client.post("/auth/register", json={
            "org_name": "Northside Clinic", "full_name": "Nora North",
            "email": EMAIL, "password": OLD_PASSWORD,
        })
        # Registration creates an owner; mark the invite accepted so the
        # account looks like an ordinary established one.
        db = Session()
        try:
            u = db.query(models.User).filter(models.User.email == EMAIL).first()
            u.invite_accepted_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()
        yield client, Session, sent


def _user(Session, email=EMAIL):
    db = Session()
    try:
        return db.query(models.User).filter(models.User.email == email).first()
    finally:
        db.close()


def _issue(Session, email=EMAIL, minutes=60):
    """Put a known-good reset token on the account."""
    token, token_hash = generate_password_reset_token()
    db = Session()
    try:
        u = db.query(models.User).filter(models.User.email == email).first()
        u.reset_token_hash = token_hash
        u.reset_expires_at = datetime.utcnow() + timedelta(minutes=minutes)
        db.commit()
    finally:
        db.close()
    return token


def _login(client, password):
    return client.post(
        "/auth/login", data={"username": EMAIL, "password": password}
    )


# ================================================ no user enumeration

def test_a_known_and_an_unknown_address_look_identical(ctx):
    """
    The property that matters most here. Anything that distinguishes them
    turns this into a way to enumerate a clinic's staff.
    """
    client, _S, _sent = ctx
    known = client.post("/auth/request-password-reset", json={"email": EMAIL})
    unknown = client.post(
        "/auth/request-password-reset", json={"email": "nobody@example.com"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_an_unknown_address_produces_no_email_and_no_token(ctx):
    client, S, sent = ctx
    client.post("/auth/request-password-reset", json={"email": "nobody@example.com"})
    assert sent == []
    assert _user(S).reset_token_hash is None


def test_a_known_address_gets_a_link(ctx):
    client, S, sent = ctx
    r = client.post("/auth/request-password-reset", json={"email": EMAIL})
    assert r.status_code == 200
    assert _user(S).reset_token_hash is not None
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == EMAIL
    assert "reset" in subject.lower()
    # Derived from the configured CORS origin, like the invite link.
    assert "/reset-password?token=" in body


def test_the_stored_token_is_only_a_hash(ctx):
    """A database read must not be able to mint a working link."""
    client, S, sent = ctx
    client.post("/auth/request-password-reset", json={"email": EMAIL})
    body = sent[0][2]
    plaintext = body.split("token=")[1].split()[0].strip()
    stored = _user(S).reset_token_hash
    assert stored != plaintext
    assert len(stored) == 64  # sha256 hex


# ====================================================== resetting

def test_a_valid_token_changes_the_password(ctx):
    client, S, _sent = ctx
    token = _issue(S)
    r = client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]

    assert verify_password(NEW_PASSWORD, _user(S).hashed_password)
    assert _login(client, NEW_PASSWORD).status_code == 200
    assert _login(client, OLD_PASSWORD).status_code == 401


def test_a_token_works_exactly_once(ctx):
    client, S, _sent = ctx
    token = _issue(S)
    assert client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    ).status_code == 200
    again = client.post(
        "/auth/reset-password", json={"token": token, "password": "An0therPass!"}
    )
    assert again.status_code == 400
    # And the second attempt did not take effect.
    assert verify_password(NEW_PASSWORD, _user(S).hashed_password)


def test_an_expired_token_is_refused(ctx):
    client, S, _sent = ctx
    token = _issue(S, minutes=-1)
    r = client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert r.status_code == 400
    assert verify_password(OLD_PASSWORD, _user(S).hashed_password)


@pytest.mark.parametrize("case", ["unknown", "expired", "used"])
def test_every_failure_reads_the_same(ctx, case):
    """
    Unknown, expired and already-spent are indistinguishable, so the
    endpoint cannot be used to probe which tokens ever existed.
    """
    client, S, _sent = ctx
    if case == "unknown":
        token = "not-a-real-reset-token"
    elif case == "expired":
        token = _issue(S, minutes=-1)
    else:
        token = _issue(S)
        client.post("/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})

    r = client.post("/auth/reset-password", json={"token": token, "password": "Wh4tever!"})
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "That reset link is no longer valid. Please request a new one."
    )


def test_a_weak_password_is_refused(ctx):
    """Same strength rule as accepting an invitation."""
    client, S, _sent = ctx
    token = _issue(S)
    r = client.post("/auth/reset-password", json={"token": token, "password": "short"})
    assert r.status_code == 422
    assert verify_password(OLD_PASSWORD, _user(S).hashed_password)


# ===================================================== the boundaries

def test_an_unaccepted_invitation_cannot_be_reset(ctx):
    """
    There is no password to reset yet, and resetting one this way would
    sidestep the invitation flow entirely.
    """
    client, S, sent = ctx
    db = S()
    try:
        db.add(models.User(
            org_id=_user(S).org_id, full_name="Not Yet", email="pending@example.com",
            hashed_password="x", role="medical_staff", is_active=True,
            invite_token_hash="a" * 64, invited_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    client.post("/auth/request-password-reset", json={"email": "pending@example.com"})
    assert sent == []


def test_a_deactivated_account_cannot_be_reset(ctx):
    client, S, sent = ctx
    db = S()
    try:
        db.query(models.User).filter(models.User.email == EMAIL).first().is_active = False
        db.commit()
    finally:
        db.close()
    client.post("/auth/request-password-reset", json={"email": EMAIL})
    assert sent == []


def test_a_suspended_clinic_cannot_complete_a_reset(ctx):
    """A suspended clinic is cut off everywhere else; not the one door left open."""
    client, S, _sent = ctx
    token = _issue(S)
    db = S()
    try:
        org_id = db.query(models.User).filter(models.User.email == EMAIL).first().org_id
        db.query(models.Organization).filter(
            models.Organization.id == org_id).first().suspended = True
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert r.status_code == 400
    assert verify_password(OLD_PASSWORD, _user(S).hashed_password)


def test_the_request_endpoint_is_rate_limited(ctx, monkeypatch):
    """
    The response reveals nothing, so volume is the only thing left to
    abuse.
    """
    client, _S, _sent = ctx
    monkeypatch.setattr(settings, "password_reset_rate_limit_attempts", 3)
    codes = [
        client.post("/auth/request-password-reset", json={"email": EMAIL}).status_code
        for _ in range(5)
    ]
    assert 429 in codes, codes


def test_both_halves_are_audited_separately(ctx):
    """
    "Somebody asked" and "the password changed" are different facts, and
    only the second means a credential moved.
    """
    client, S, _sent = ctx
    client.post("/auth/request-password-reset", json={"email": EMAIL})
    token = _issue(S)
    client.post("/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})

    db = S()
    try:
        actions = {e.action for e in db.query(models.AuditLogEntry).all()}
    finally:
        db.close()
    assert "auth.password_reset_requested" in actions
    assert "auth.password_reset" in actions


def test_the_audit_never_records_the_token_or_password(ctx):
    client, S, sent = ctx
    client.post("/auth/request-password-reset", json={"email": EMAIL})
    token = _issue(S)
    client.post("/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})

    db = S()
    try:
        blob = " ".join(
            f"{e.detail or ''} {e.who or ''}" for e in db.query(models.AuditLogEntry).all()
        )
    finally:
        db.close()
    assert token not in blob
    assert NEW_PASSWORD not in blob
