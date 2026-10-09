import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .security import (
    decode_access_token,
    decode_agent_token,
    decode_super_admin_token,
    hash_api_key,
    mask_api_key,
)

logger = logging.getLogger("auth")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
super_admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/admin/auth/login", auto_error=False)


def _attach_impersonator(db: Session, user: models.User, payload: dict) -> models.User:
    """
    Marks a session that is really a super admin acting inside this org.

    Impersonation mints an ordinary staff token so every existing route
    works unchanged (see `admin.impersonate_organization`) — which also
    meant the clinic's own audit log attributed our actions to its admin.
    The token now carries an `imp` claim naming the super admin, and
    `audit_service` renders that as a `platform` actor.

    `imp` is an extra claim on a staff token, not a new token family:
    `decode_access_token` rejects anything carrying `typ`, and that is
    still the only barrier between the three families. A forged `imp`
    would need the signing key, at which point it is not the weak point.

    The attribute is always set, so no caller has to guard for it.
    """
    user.impersonated_by = None
    impersonator_id = payload.get("imp")
    if impersonator_id:
        admin = (
            db.query(models.SuperAdmin)
            .filter(models.SuperAdmin.id == impersonator_id)
            .first()
        )
        # A revoked or deleted super admin still gets attributed — the
        # clinic needs to know the session was ours, and the id is the
        # thread back to `super_admin_audit_log`.
        user.impersonated_by = admin.email if admin else f"super_admin:{impersonator_id}"
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or not user.is_active:
        raise credentials_exception
    if user.organization is not None and user.organization.suspended:
        raise HTTPException(status_code=403, detail="This organization has been suspended")
    return _attach_impersonator(db, user, payload)


def get_current_super_admin(
    token: str = Depends(super_admin_oauth2_scheme), db: Session = Depends(get_db)
) -> models.SuperAdmin:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    payload = decode_super_admin_token(token)
    if payload is None:
        raise credentials_exception
    admin_id = payload.get("sub")
    if admin_id is None:
        raise credentials_exception
    admin = db.query(models.SuperAdmin).filter(models.SuperAdmin.id == admin_id).first()
    if admin is None or not admin.is_active:
        raise credentials_exception
    return admin


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != models.UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class OrgContext:
    """
    Resolved tenant context for a request, independent of *how* the caller
    authenticated. Lets a handful of read endpoints (e.g. patient lookup)
    be shared between dashboard staff (JWT) and trusted services like the
    voice agent (API key) without duplicating routes or business logic.
    """

    def __init__(self, org_id: str, actor: str, user: Optional[models.User] = None):
        self.org_id = org_id
        self.actor = actor  # "user" | "service"
        self.user = user


def get_org_context(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> OrgContext:
    """
    Authenticates via X-API-Key (service callers, e.g. the voice agent)
    if present, otherwise falls back to the standard JWT bearer token
    (dashboard users). Raises 401 if neither is valid.
    """
    if x_api_key:
        hashed = hash_api_key(x_api_key)
        key_row = (
            db.query(models.APIKey)
            .filter(models.APIKey.hashed_key == hashed, models.APIKey.revoked == False)  # noqa: E712 - SQL Server BIT compatibility
            .first()
        )
        if not key_row:
            logger.warning(f"Rejected API key attempt: {mask_api_key(x_api_key)}")
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        org = db.query(models.Organization).filter(models.Organization.id == key_row.org_id).first()
        if org is not None and org.suspended:
            raise HTTPException(status_code=403, detail="This organization has been suspended")
        return OrgContext(org_id=key_row.org_id, actor="service")

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        payload = decode_access_token(token)

        if payload is None:
            # Not a staff token. It may be an org-scoped voice-agent token
            # (Phase 2), issued by /agent/resolve-tenant once the agent has
            # proved which clinic a dialled number belongs to.
            #
            # This is safe to try only here: decode_access_token rejects any
            # token carrying a "typ" claim, so an agent token can never
            # authenticate get_current_user / require_admin — exactly the
            # same barrier that keeps super-admin tokens out of these routes.
            agent_payload = decode_agent_token(token)
            if agent_payload is None:
                raise HTTPException(status_code=401, detail="Could not validate credentials")
            org = (
                db.query(models.Organization)
                .filter(models.Organization.id == agent_payload["org_id"])
                .first()
            )
            if org is None:
                raise HTTPException(status_code=401, detail="Could not validate credentials")
            if org.suspended:
                raise HTTPException(status_code=403, detail="This organization has been suspended")
            return OrgContext(org_id=org.id, actor="service")

        user_id = payload.get("sub")
        user = db.query(models.User).filter(models.User.id == user_id).first() if user_id else None
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Could not validate credentials")
        if user.organization is not None and user.organization.suspended:
            raise HTTPException(status_code=403, detail="This organization has been suspended")
        return OrgContext(
            org_id=user.org_id, actor="user", user=_attach_impersonator(db, user, payload)
        )

    raise HTTPException(status_code=401, detail="Missing credentials")