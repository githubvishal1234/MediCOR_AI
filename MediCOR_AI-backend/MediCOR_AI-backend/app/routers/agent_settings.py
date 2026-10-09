from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import audit_service, escalation_service, models, schemas
from ..database import get_db
from ..deps import get_current_user, require_admin

router = APIRouter(prefix="/agent-settings", tags=["agent-settings"])


def _get_or_create_settings(db: Session, org_id: str) -> models.AgentSettings:
    settings_row = (
        db.query(models.AgentSettings).filter(models.AgentSettings.org_id == org_id).first()
    )
    if not settings_row:
        settings_row = models.AgentSettings(org_id=org_id)
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    return settings_row


@router.get("", response_model=schemas.AgentSettingsOut)
def get_settings(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return _get_or_create_settings(db, user.org_id)


@router.put("", response_model=schemas.AgentSettingsOut)
def update_settings(
    payload: schemas.AgentSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    settings_row = _get_or_create_settings(db, user.org_id)
    data = payload.model_dump(exclude_unset=True)
    before = {k: getattr(settings_row, k, None) for k in data}
    for k, v in data.items():
        setattr(settings_row, k, v)
    # This is the highest-consequence settings change in the product: it
    # alters what the AI says to patients on a live call. Field names
    # only, not the new greeting text — the settings row already holds
    # the current value, and the log is read by more people than it is.
    audit_service.record(
        db, user.org_id, "agent_settings.updated", user=user,
        target_type="agent_settings", target_id=settings_row.id,
        detail=audit_service.changed_fields(before, data), request=request,
    )
    db.commit()
    db.refresh(settings_row)
    return settings_row


@router.get("/routing-rule-conditions", response_model=list[str])
def list_routing_rule_conditions(
    _: models.User = Depends(get_current_user),
):
    """
    The condition types a rule may use, served rather than hardcoded in
    the UI.

    F14 learned this the hard way: the webhook settings screen offered
    three events that no part of the codebase emitted, because the list
    lived in the frontend. A screen offering `outside_hours` when the
    server does not evaluate it is the same defect.

    Declared before `/routing-rules/{rule_id}` would ever be consulted --
    it is a distinct literal path, so ordering is not load-bearing here,
    but it reads next to the list it belongs to.
    """
    return list(escalation_service.CONDITION_TYPES)


@router.get("/routing-rules", response_model=list[schemas.RoutingRuleOut])
def list_routing_rules(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    return db.query(models.RoutingRule).filter(models.RoutingRule.org_id == user.org_id).all()


@router.post("/routing-rules", response_model=schemas.RoutingRuleOut)
def create_routing_rule(
    payload: schemas.RoutingRuleBase,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    rule = models.RoutingRule(org_id=user.org_id, **payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/routing-rules/{rule_id}", response_model=schemas.RoutingRuleOut)
def update_routing_rule(
    rule_id: str,
    payload: schemas.RoutingRuleUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    rule = (
        db.query(models.RoutingRule)
        .filter(models.RoutingRule.id == rule_id, models.RoutingRule.org_id == user.org_id)
        .first()
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(rule, k, v)

    # Checked on the merged row, which is the only thing that knows the
    # answer: a PATCH setting condition_type="keyword" may be relying on
    # keywords already stored, and a PATCH clearing keywords may be
    # leaving behind a rule whose type still needs them. Either way the
    # result is a rule that can never fire -- silently, and forever,
    # which is the exact defect shape this codebase keeps closing.
    if escalation_service.condition_of(rule) == "keyword" and not (
        escalation_service.parse_keywords(rule.keywords)
    ):
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail=(
                "A keyword rule needs at least one keyword, or it can never "
                "match. Use the 'model' condition for a rule written as a "
                "sentence."
            ),
        )

    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/routing-rules/{rule_id}", status_code=204)
def delete_routing_rule(
    rule_id: str, db: Session = Depends(get_db), user: models.User = Depends(require_admin)
):
    rule = (
        db.query(models.RoutingRule)
        .filter(models.RoutingRule.id == rule_id, models.RoutingRule.org_id == user.org_id)
        .first()
    )
    if rule:
        db.delete(rule)
        db.commit()
    return None
