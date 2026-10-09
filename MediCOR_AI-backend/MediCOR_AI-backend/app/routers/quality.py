"""
Call quality — AI-7.

Read and trigger side for the LLM-as-judge evaluation in
`app/evaluation_service.py`. Staff-only (`get_current_user`): this is a
clinic reviewing its own AI, and the voice agent has no reason to read or
run it — so it is deliberately not exposed through `get_org_context`.

Running a batch is admin-only. It costs money per call judged, and an
unbounded "evaluate everything" button reachable by any staff member is
a bill waiting to happen.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import evaluation_service, llm_service, models, schemas
from ..database import get_db
from ..deps import get_current_user, require_admin

logger = logging.getLogger("quality")

router = APIRouter(prefix="/quality", tags=["quality"])

# Upper bound on one batch. Evaluation is a paid model call per call
# judged, so the endpoint caps this regardless of what is asked for.
_MAX_BATCH = 50


@router.get("/feedback", response_model=schemas.FeedbackSummaryOut)
def feedback_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    What patients said, beside what the AI judge said (PF-10).

    Deliberately reported alongside the quality score rather than instead
    of it: the interesting figure is the gap between the two. A call the
    judge scored 90 and the patient scored 2 is worth more attention than
    either number alone.

    `average_rating` is None when nobody has answered, never 0 -- an
    average of nothing is not zero out of five. Same rule as
    `resolution_rate_pct`.
    """
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(models.PatientFeedback)
        .filter(
            models.PatientFeedback.org_id == user.org_id,
            models.PatientFeedback.created_at >= since,
        )
        .all()
    )
    asked = (
        db.query(func.count(models.CallLog.id))
        .filter(
            models.CallLog.org_id == user.org_id,
            models.CallLog.feedback_requested_at.isnot(None),
            models.CallLog.feedback_requested_at >= since,
        )
        .scalar()
        or 0
    )

    ratings = [r.rating for r in rows if r.rating is not None]
    # Counts per score, so a bimodal split stays visible instead of being
    # averaged into a comfortable middle.
    distribution = {str(n): 0 for n in range(
        schemas.FEEDBACK_RATING_MIN, schemas.FEEDBACK_RATING_MAX + 1
    )}
    for r in ratings:
        key = str(r)
        if key in distribution:
            distribution[key] += 1

    return schemas.FeedbackSummaryOut(
        responses=len(ratings),
        average_rating=(round(sum(ratings) / len(ratings), 2) if ratings else None),
        distribution=distribution,
        asked=asked,
    )


@router.get("/summary", response_model=schemas.EvaluationSummaryOut)
def quality_summary(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Aggregate quality across every evaluated call for this clinic."""
    return schemas.EvaluationSummaryOut(**evaluation_service.summarize(db, user.org_id))


@router.get("/evaluations", response_model=list[schemas.CallEvaluationWithCallOut])
def list_evaluations(
    needs_review: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Evaluated calls, newest first.

    `needs_review=true` narrows to the work list — anything with a
    hallucination, a policy violation, or a score under 60 that nobody has
    signed off yet. That filter is applied in Python rather than SQL
    because `needs_review` is a derived property combining three columns;
    at the volume one clinic produces that is not worth denormalising.
    """
    rows = (
        db.query(models.CallEvaluation)
        .filter(models.CallEvaluation.org_id == user.org_id)
        .order_by(models.CallEvaluation.evaluated_at.desc())
        .all()
    )
    if needs_review:
        rows = [r for r in rows if r.needs_review]
    page = rows[max(offset, 0) : max(offset, 0) + min(max(limit, 1), 200)]

    calls = {
        c.id: c
        for c in db.query(models.CallLog)
        .filter(models.CallLog.id.in_([r.call_id for r in page] or [""]))
        .all()
    }

    out = []
    for row in page:
        call = calls.get(row.call_id)
        out.append(
            schemas.CallEvaluationWithCallOut(
                id=row.id,
                call_id=row.call_id,
                score=row.score,
                goal_achieved=row.goal_achieved,
                hallucination_detected=row.hallucination_detected,
                policy_violations=row.policy_violations,
                conciseness=row.conciseness,
                reasoning=row.reasoning,
                judge_model=row.judge_model,
                evaluated_at=row.evaluated_at,
                reviewed_at=row.reviewed_at,
                needs_review=row.needs_review,
                patient_name=call.patient_name if call else None,
                occurred_at=call.occurred_at if call else None,
                outcome=call.outcome if call else None,
            )
        )
    return out


@router.get("/calls/{call_id}", response_model=schemas.CallEvaluationOut)
def get_call_evaluation(
    call_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """The most recent evaluation for one call."""
    row = (
        db.query(models.CallEvaluation)
        .filter(
            models.CallEvaluation.call_id == call_id,
            models.CallEvaluation.org_id == user.org_id,
        )
        .order_by(models.CallEvaluation.evaluated_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="This call has not been evaluated")
    return row


@router.post("/evaluations/{evaluation_id}/reviewed", response_model=schemas.CallEvaluationOut)
def mark_reviewed(
    evaluation_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Signs off a flagged call, removing it from the review queue.

    Idempotent — re-marking keeps the original timestamp rather than
    moving it forward.
    """
    row = (
        db.query(models.CallEvaluation)
        .filter(
            models.CallEvaluation.id == evaluation_id,
            models.CallEvaluation.org_id == user.org_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    if row.reviewed_at is None:
        row.reviewed_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
    return row


@router.post("/run", response_model=schemas.EvaluationRunOut)
def run_evaluation(
    background_tasks: BackgroundTasks,
    limit: int = 20,
    admin: models.User = Depends(require_admin),
):
    """
    Judges up to `limit` recent, not-yet-evaluated calls in the background.

    Admin-only and capped: each judged call is a paid model request.
    """
    capped = min(max(limit, 1), _MAX_BATCH)
    if not llm_service.is_enabled():
        return schemas.EvaluationRunOut(
            queued=False,
            limit=capped,
            detail=(
                "No LLM is configured, so calls cannot be evaluated. "
                "Set GROQ_API_KEY to enable quality scoring."
            ),
        )

    evaluation_service.schedule_batch(background_tasks, admin.org_id, capped)
    logger.info("Queued evaluation of up to %d calls for org=%s", capped, admin.org_id)
    return schemas.EvaluationRunOut(
        queued=True,
        limit=capped,
        detail=f"Evaluating up to {capped} recent calls. Refresh in a moment to see scores.",
    )
