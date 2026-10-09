import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import audit_service, models, notification_service, schemas, timezones
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..rate_limit import (
    clear_account_failures,
    enforce_account_rate_limit,
    enforce_rate_limit,
    record_account_failure,
)
from ..security import (
    create_access_token,
    generate_password_reset_token,
    hash_invite_token,
    hash_password,
    hash_password_reset_token,
    verify_password,
)

logger = logging.getLogger("auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# Bucket name for the per-account (email-keyed) failed-login counter.
_LOGIN_ACCOUNT_BUCKET = "login"


@router.post("/register", response_model=schemas.LoginOut)
def register(payload: schemas.RegisterIn, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(
        request,
        bucket="register",
        max_attempts=settings.register_rate_limit_attempts,
        window_seconds=settings.register_rate_limit_window_seconds,
    )

    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Reject a bad timezone at signup rather than silently seating the
    # clinic on UTC — every slot it offers would be wrong by its offset.
    tz_name = (payload.timezone or "").strip()
    if tz_name and not timezones.is_valid(tz_name):
        raise HTTPException(
            status_code=422,
            detail=f"'{tz_name}' is not a recognised timezone name. Use an IANA name such as 'Asia/Kolkata'.",
        )

    try:
        org = models.Organization(name=payload.org_name, timezone=tz_name or "UTC")
        db.add(org)
        db.flush()

        user = models.User(
            org_id=org.id,
            email=payload.email,
            hashed_password=hash_password(payload.password),
            full_name=payload.full_name,
            role=models.UserRole.admin,
        )
        db.add(user)

        # Per-org defaults. Structure only — deliberately NO placeholder
        # doctors, services or fees: the voice agent reads these straight
        # out to patients, and inventing a "Dr. Placeholder" or a made-up
        # consultation fee is worse than an empty profile the clinic is
        # prompted to fill in (see GET /onboarding/status).
        db.add(models.AgentSettings(org_id=org.id))
        db.add(models.Webhook(org_id=org.id))
        db.add(models.ClinicInfo(org_id=org.id))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Registration failed while creating org/user")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    token = create_access_token({"sub": user.id})
    return schemas.LoginOut(access_token=token)


@router.post("/login", response_model=schemas.LoginOut)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    enforce_rate_limit(
        request,
        bucket="login",
        max_attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    # Second, independent throttle keyed on the email being tried rather
    # than the source IP — this is the one that sees a distributed
    # credential-stuffing run against a single known account.
    enforce_account_rate_limit(
        form_data.username,
        bucket=_LOGIN_ACCOUNT_BUCKET,
        max_attempts=settings.login_account_rate_limit_attempts,
        window_seconds=settings.login_account_rate_limit_window_seconds,
    )

    def _reject(known_user: models.User | None, reason: str) -> HTTPException:
        record_account_failure(
            form_data.username,
            bucket=_LOGIN_ACCOUNT_BUCKET,
            window_seconds=settings.login_account_rate_limit_window_seconds,
        )
        # HV-1: a failed attempt is recorded only when the email belongs
        # to a real account. `audit_log.org_id` is NOT NULL, so an attempt
        # against an unknown address has no clinic to file it under — and
        # inventing one, or writing it to every org, would turn the
        # Security page into the account-enumeration oracle the identical
        # 401 below exists to prevent. Unknown addresses stay in the
        # application log, where the rate limiter already tracks them.
        if known_user is not None:
            audit_service.record(
                db,
                known_user.org_id,
                "auth.login_failed",
                who=known_user.email,
                actor_type=audit_service.ACTOR_ANONYMOUS,
                target_type="user",
                target_id=known_user.id,
                detail=reason,
                status=audit_service.STATUS_BLOCKED,
                request=request,
                commit=True,
            )
        # Identical response for "no such user", "wrong password", and
        # "deactivated account" — no enumeration signal.
        return HTTPException(status_code=401, detail="Incorrect email or password")

    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        logger.info(f"Failed login attempt for email={form_data.username}")
        raise _reject(user, "Incorrect password")
    if not user.is_active:
        raise _reject(user, "Account is deactivated")

    clear_account_failures(form_data.username, bucket=_LOGIN_ACCOUNT_BUCKET)
    token = create_access_token({"sub": user.id})
    audit_service.record(
        db,
        user.org_id,
        "auth.login",
        user=user,
        target_type="user",
        target_id=user.id,
        status=audit_service.STATUS_SUCCESS,
        request=request,
        commit=True,
    )
    return schemas.LoginOut(access_token=token)


def _reset_url(token: str) -> str:
    """
    Absolute link to the frontend's reset screen.

    Derived from the first configured CORS origin -- the same source
    `users._invite_url` uses, because it is the only place the backend is
    told where its frontend lives, and a second setting answering the same
    question is a second thing to get wrong.

    Empty when unset rather than a guessed hostname: the email then says
    to contact an administrator instead of carrying a dead link.
    """
    origins = settings.cors_origin_list
    if not origins:
        return ""
    return f"{origins[0].rstrip('/')}/reset-password?token={token}"


_RESET_ACK = (
    "If that address has an account, we've sent a link to reset the password."
)


@router.post("/request-password-reset", response_model=schemas.PasswordResetAck)
def request_password_reset(
    payload: schemas.PasswordResetRequestIn,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Start a password reset (GAP-U1).

    Before this, a locked-out staff member needed an administrator or a
    direct database edit. There was no self-service path at all.

    **The response is identical whether or not the address is
    registered.** Anything else turns this endpoint into a way to
    enumerate a clinic's staff, which is the same reasoning behind the
    single error `login` and `accept-invite` return. That is also why the
    work happens after the check rather than being skipped early with a
    different status.

    Rate limited per client, because the response reveals nothing and so
    volume is the only thing left to abuse.
    """
    enforce_rate_limit(
        request,
        bucket="password_reset",
        max_attempts=settings.password_reset_rate_limit_attempts,
        window_seconds=settings.password_reset_rate_limit_window_seconds,
    )

    email = (payload.email or "").strip().lower()
    user = (
        db.query(models.User)
        .filter(func.lower(models.User.email) == email)
        .first()
    )

    # An inactive account, or one whose invitation was never accepted, is
    # not resettable: there is no password to reset yet, and activating it
    # this way would sidestep the invitation flow entirely.
    if user is not None and user.is_active and user.invite_accepted_at is not None:
        token, token_hash = generate_password_reset_token()
        user.reset_token_hash = token_hash
        user.reset_requested_at = datetime.utcnow()
        user.reset_expires_at = datetime.utcnow() + timedelta(
            minutes=settings.password_reset_expire_minutes
        )
        org = (
            db.query(models.Organization)
            .filter(models.Organization.id == user.org_id)
            .first()
        )
        audit_service.record(
            db, user.org_id, "auth.password_reset_requested",
            who=user.email, actor_type=audit_service.ACTOR_ANONYMOUS,
            target_type="user", target_id=user.id,
            status=audit_service.STATUS_LOGGED,
            request=request,
        )
        db.commit()

        reset_url = _reset_url(token)
        notification_service.schedule_password_reset_email(
            background_tasks, user.email, reset_url, org.name if org else "your clinic"
        )

    return schemas.PasswordResetAck(detail=_RESET_ACK)


@router.post("/reset-password", response_model=schemas.LoginOut)
def reset_password(
    payload: schemas.PasswordResetIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Set a new password using a reset token.

    One identical error for an unknown, expired or already-used token --
    the same rule `accept-invite` follows, and for the same reason.

    **Existing sessions are not invalidated, and that is a known
    limitation rather than an oversight.** The JWTs this product issues
    are stateless and carry no revocation list, so a token already in
    somebody's hands stays valid until it expires on its own. Anyone
    resetting a password because they believe it was compromised should be
    told that; building revocation means giving every request a database
    lookup, which is a deliberate architectural decision and not one to
    make inside a reset endpoint.
    """
    enforce_rate_limit(
        request,
        bucket="password_reset",
        max_attempts=settings.password_reset_rate_limit_attempts,
        window_seconds=settings.password_reset_rate_limit_window_seconds,
    )

    invalid = HTTPException(
        status_code=400,
        detail="That reset link is no longer valid. Please request a new one.",
    )

    token_hash = hash_password_reset_token(payload.token.strip())
    user = (
        db.query(models.User)
        .filter(models.User.reset_token_hash == token_hash)
        .first()
    )
    if user is None or not user.is_active:
        raise invalid
    if user.reset_expires_at is None or user.reset_expires_at < datetime.utcnow():
        raise invalid

    org = (
        db.query(models.Organization)
        .filter(models.Organization.id == user.org_id)
        .first()
    )
    if org is None or org.suspended:
        # A suspended clinic is cut off everywhere else; this is not the
        # one door left open.
        raise invalid

    user.hashed_password = hash_password(payload.password)
    # Single-use: cleared so a spent link is indistinguishable from one
    # that never existed.
    user.reset_token_hash = None
    user.reset_expires_at = None

    audit_service.record(
        db, user.org_id, "auth.password_reset", user=user,
        target_type="user", target_id=user.id,
        status=audit_service.STATUS_LOGGED,
        request=request,
    )
    db.commit()

    return schemas.LoginOut(access_token=create_access_token({"sub": user.id}))


@router.post("/accept-invite", response_model=schemas.LoginOut)
def accept_invite(
    payload: schemas.AcceptInviteIn, request: Request, db: Session = Depends(get_db)
):
    """
    Completes a staff invitation: sets the password and activates the account.

    Unauthenticated by necessity — the invited person has no credentials
    yet; the token *is* the credential. It is single-use (cleared on
    success), time-limited, and matched by SHA-256 hash, so the database
    never holds anything that could be replayed as a link.

    Every failure returns the same 400. Distinguishing "unknown token"
    from "expired" from "already accepted" would turn this into an oracle
    for probing which invites exist.
    """
    enforce_rate_limit(
        request,
        bucket="accept_invite",
        max_attempts=settings.accept_invite_rate_limit_attempts,
        window_seconds=settings.accept_invite_rate_limit_window_seconds,
    )

    invalid = HTTPException(
        status_code=400,
        detail="That invitation link is invalid or has expired. Ask your clinic admin to send a new one.",
    )

    token_hash = hash_invite_token(payload.token.strip())
    user = (
        db.query(models.User)
        .filter(models.User.invite_token_hash == token_hash)
        .first()
    )
    if user is None or user.invite_accepted_at is not None:
        raise invalid
    if user.invite_expires_at is None or user.invite_expires_at < datetime.utcnow():
        raise invalid

    try:
        user.hashed_password = hash_password(payload.password)
        user.is_active = True
        user.invite_accepted_at = datetime.utcnow()
        # Single-use: clear the token so the link cannot be replayed to
        # reset this account's password later.
        user.invite_token_hash = None
        user.invite_expires_at = None
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to accept invitation")
        raise HTTPException(status_code=500, detail="Could not complete the invitation. Please try again.")

    logger.info("Invitation accepted for user=%s org=%s", user.id, user.org_id)
    audit_service.record(
        db,
        user.org_id,
        "auth.invite_accepted",
        user=user,
        target_type="user",
        target_id=user.id,
        detail=f"role={user.role.value if hasattr(user.role, 'value') else user.role}",
        request=request,
        commit=True,
    )
    return schemas.LoginOut(access_token=create_access_token({"sub": user.id}))


@router.get("/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user