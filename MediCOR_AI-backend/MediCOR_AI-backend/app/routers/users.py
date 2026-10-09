"""
Clinic staff management — HV-2.

Before this router there was no way for a clinic admin to add a colleague.
The only user-creating path in the product was `POST /auth/register`,
which creates a whole new *organization* — so a receptionist who signed up
got their own separate clinic rather than a seat at an existing one. That
made the three-role model (`admin` / `medical_staff` / `ai_agent`)
unusable in practice: every real user was the admin of a one-person org,
which is also why enforcing RBAC (QW-7) was pointless until now.

Design notes:

  * An invited user is a real `users` row from the moment the invite is
    sent, created with `is_active=False` and an unusable password. No
    separate Invitation table — see the comment on User.invite_* in
    models.py for why.

  * Accepting an invite lives in `auth.py`, not here: it is the one
    unauthenticated step, and it belongs with the other credential
    endpoints.

  * Every write is admin-only and org-scoped. `_get_org_user_or_404`
    enforces the tenant boundary the same way the other routers'
    `_get_*_or_404` helpers do — a user id from another org is a 404, not
    a 403, so this cannot be used to probe for valid ids.

  * Two lockout guards, both of which exist because a clinic locking
    itself out of its own dashboard has no self-service recovery: an
    admin cannot demote or deactivate themselves, and the last active
    admin in an org cannot be demoted, deactivated, or removed.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import audit_service, models, notification_service, schemas, usage_service
from ..config import settings
from ..database import get_db
from ..deps import require_admin
from ..security import generate_invite_token, hash_password

logger = logging.getLogger("users")

router = APIRouter(prefix="/users", tags=["users"])


def _get_org_user_or_404(db: Session, user_id: str, org_id: str) -> models.User:
    user = (
        db.query(models.User)
        .filter(models.User.id == user_id, models.User.org_id == org_id)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _active_admin_count(db: Session, org_id: str, excluding: Optional[str] = None) -> int:
    q = db.query(models.User).filter(
        models.User.org_id == org_id,
        models.User.role == models.UserRole.admin,
        models.User.is_active == True,  # noqa: E712 - SQL Server BIT compatibility
    )
    if excluding:
        q = q.filter(models.User.id != excluding)
    return q.count()


def _guard_last_admin(db: Session, target: models.User) -> None:
    """
    Refuses a change that would leave the org with no active admin.

    There is no way back from that state without a super admin or direct
    database access, so it is blocked rather than warned about.
    """
    if target.role != models.UserRole.admin or not target.is_active:
        return
    if _active_admin_count(db, target.org_id, excluding=target.id) == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "This is the clinic's only active admin. Promote another user "
                "to admin first."
            ),
        )


def _invite_url(token: str) -> Optional[str]:
    """
    Absolute link to the frontend's accept-invite screen.

    Derived from the first configured CORS origin, which is the only place
    the backend is told where its frontend lives. Returns None when that
    is unset rather than guessing a hostname — the raw token is still
    returned, so an admin can always complete the invite manually.
    """
    origins = settings.cors_origin_list
    if not origins:
        return None
    return f"{origins[0].rstrip('/')}/accept-invite?token={token}"


def _issue_invite(user: models.User) -> str:
    """Stamps a fresh invite token onto `user`. Returns the plaintext once."""
    token, token_hash = generate_invite_token()
    user.invite_token_hash = token_hash
    user.invited_at = datetime.utcnow()
    user.invite_expires_at = datetime.utcnow() + timedelta(
        days=settings.invite_expire_days
    )
    user.invite_accepted_at = None
    return token


@router.get("", response_model=list[schemas.TeamMemberOut])
def list_team(
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """Everyone in the caller's clinic, including deactivated and pending users."""
    return (
        db.query(models.User)
        .filter(models.User.org_id == admin.org_id)
        .order_by(models.User.created_at)
        .all()
    )


@router.post("/invite", response_model=schemas.InviteOut, status_code=201)
def invite_user(
    payload: schemas.InviteIn,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """
    Creates an inactive user and issues a one-time invite token.

    The token is returned in the response and, if SMTP is configured, also
    emailed. It is not recoverable afterwards — only its hash is stored.
    """
    email = payload.email.strip().lower()

    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing is not None:
        # `email` is globally unique, so the collision may be with a user
        # at another clinic. Say only that the address is unavailable —
        # confirming an account exists elsewhere would leak membership of
        # another organization.
        if existing.org_id == admin.org_id:
            detail = "Someone with that email address is already on your team."
        else:
            detail = "That email address is not available."
        raise HTTPException(status_code=409, detail=detail)

    # HV-4: plan seat limits were stored and displayed but never checked,
    # so a plan's limits were decoration. Checked before the row is
    # created, so a refusal leaves nothing behind.
    limit_error = usage_service.check_user_limit(db, admin.org_id)
    if limit_error:
        raise HTTPException(status_code=402, detail=limit_error)

    user = models.User(
        org_id=admin.org_id,
        email=email,
        full_name=payload.full_name.strip(),
        role=models.UserRole(payload.role),
        is_active=False,
        # An unguessable, unusable password so the row is never
        # authenticatable before the invite is accepted. `is_active=False`
        # already blocks login; this is the second lock, so that flipping
        # the flag alone can never grant access without a password reset.
        hashed_password=hash_password(generate_invite_token()[0]),
    )
    token = _issue_invite(user)
    db.add(user)
    db.flush()
    audit_service.record(
        db, admin.org_id, "team.member_invited", user=admin,
        target_type="user", target_id=user.id,
        detail=f"{user.email} as {user.role.value}", request=request,
    )
    db.commit()
    db.refresh(user)

    url = _invite_url(token)
    email_sent = notification_service.schedule_invite_email(
        background_tasks,
        to_email=user.email,
        full_name=user.full_name,
        clinic_name=admin.org_name or "your clinic",
        invited_by=admin.full_name,
        invite_url=url,
    )

    logger.info(
        "User %s invited %s (role=%s) to org=%s",
        admin.id, user.email, user.role.value, admin.org_id,
    )
    return schemas.InviteOut(
        user=user, invite_token=token, invite_url=url, email_sent=email_sent
    )


@router.post("/{user_id}/resend-invite", response_model=schemas.InviteOut)
def resend_invite(
    user_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """
    Issues a **new** invite token, invalidating the previous one.

    Rotating rather than resending the same value means a link that leaked
    into a forwarded email stops working as soon as the invite is resent.
    """
    user = _get_org_user_or_404(db, user_id, admin.org_id)
    if user.invite_accepted_at is not None:
        raise HTTPException(
            status_code=400, detail="That invitation has already been accepted."
        )

    token = _issue_invite(user)
    db.commit()
    db.refresh(user)

    url = _invite_url(token)
    email_sent = notification_service.schedule_invite_email(
        background_tasks,
        to_email=user.email,
        full_name=user.full_name,
        clinic_name=admin.org_name or "your clinic",
        invited_by=admin.full_name,
        invite_url=url,
    )
    return schemas.InviteOut(
        user=user, invite_token=token, invite_url=url, email_sent=email_sent
    )


@router.patch("/{user_id}", response_model=schemas.TeamMemberOut)
def update_team_member(
    user_id: str,
    payload: schemas.TeamMemberUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """Changes a colleague's name, role, or active state."""
    user = _get_org_user_or_404(db, user_id, admin.org_id)
    data = payload.model_dump(exclude_unset=True)

    # Self-protection. An admin who demotes or deactivates themselves is
    # locked out immediately and cannot undo it.
    if user.id == admin.id:
        if data.get("role") is not None and data["role"] != models.UserRole.admin.value:
            raise HTTPException(status_code=400, detail="You cannot change your own role.")
        if data.get("is_active") is False:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")

    if data.get("role") is not None and data["role"] != user.role.value:
        if user.role == models.UserRole.admin:
            _guard_last_admin(db, user)
        user.role = models.UserRole(data["role"])

    if data.get("is_active") is False and user.is_active:
        _guard_last_admin(db, user)
        user.is_active = False
    elif data.get("is_active") is True:
        if user.invite_pending:
            raise HTTPException(
                status_code=400,
                detail="That invitation hasn't been accepted yet — resend it instead.",
            )
        user.is_active = True

    if data.get("full_name"):
        user.full_name = data["full_name"].strip()

    # Role and activation are the two that matter here: they change what
    # this account can reach, so they are named explicitly rather than
    # folded into a generic "changed" list.
    changes = []
    if data.get("role") is not None:
        changes.append(f"role={data['role']}")
    if data.get("is_active") is not None:
        changes.append("activated" if data["is_active"] else "deactivated")
    if data.get("full_name"):
        changes.append("name")
    audit_service.record(
        db, admin.org_id, "team.member_changed", user=admin,
        target_type="user", target_id=user.id,
        detail=f"{user.email}: " + (", ".join(changes) or "no change"),
        request=request,
    )
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def deactivate_team_member(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """
    Soft-deactivates a user. The row is kept.

    Deliberately not a hard delete: `SupportTicket.user_id` and the audit
    trail reference users, and a clinic's record of who did what must
    survive that person leaving.
    """
    user = _get_org_user_or_404(db, user_id, admin.org_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    if not user.is_active and user.invite_accepted_at is not None:
        return
    _guard_last_admin(db, user)

    user.is_active = False
    # Kill any outstanding invite too, so a pending link cannot be used to
    # activate an account the admin has just revoked.
    user.invite_token_hash = None
    audit_service.record(
        db, admin.org_id, "team.member_changed", user=admin,
        target_type="user", target_id=user.id,
        detail=f"{user.email}: deactivated", request=request,
    )
    db.commit()
