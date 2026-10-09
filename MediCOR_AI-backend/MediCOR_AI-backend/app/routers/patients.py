import logging
import random
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import audit_service, ehr_service, models, schemas, usage_service
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context, require_admin

logger = logging.getLogger("patients")

router = APIRouter(prefix="/patients", tags=["patients"])


# Characters people put in phone numbers that carry no information.
_PHONE_NOISE = ("+", "-", " ", "(", ")", ".", "/", " ")

# The shortest suffix allowed to identify a line. A national subscriber
# number is 10 digits in both India and the US; 7 is the floor below
# which a fragment starts matching unrelated people, and matching the
# wrong patient is far worse than matching none.
_MIN_PHONE_DIGITS = 7


def _phone_digits(value: Optional[str]) -> str:
    """Everything that is not a digit, removed."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _phone_digits_sql(column):
    """
    The same normalisation, expressed in SQL.

    Nested REPLACE rather than a regex because SQL Server has none, and
    a stored computed column would be a schema change for a lookup that
    is already a table scan. This does not make the query any less
    indexable than the `ILIKE '%term%'` beside it, which cannot use an
    index either.
    """
    expr = column
    for ch in _PHONE_NOISE:
        expr = func.replace(expr, ch, "")
    return expr


def _same_person_key(name: Optional[str]) -> str:
    """
    A name reduced for comparison: case and spacing only.

    Deliberately NOT fuzzy. "Krishna Kumar" and "K. Kumar" may well be
    one person, and deciding that they are is a clinical guess presented
    as a fact -- the same reasoning that keeps repeat-prescription
    matching to an exact medication name. This catches the case that
    actually happened: the identical name, typed or transcribed twice.
    """
    return " ".join((name or "").split()).lower()


def find_existing_patient(
    db: Session, org_id: str, name: Optional[str], phone: Optional[str]
) -> Optional[models.Patient]:
    """
    The patient this registration would duplicate, if there is one.

    WHY BOTH NAME AND PHONE

    Phone alone is not enough, and getting that wrong would be worse than
    the problem: households share a handset, so a clinic must be able to
    register a spouse or a child on one number. `GET /patients` already
    returns both of them for one search, and the agent's "exactly one
    match" rule is built on that being possible.

    So a duplicate is the same *name* on the same *line*. That is what
    the real ones were: two "Krishna" rows, one number, no date of birth,
    created eighty minutes apart because a caller-id lookup could not see
    the first.

    Archived patients are excluded on purpose. An archived row was
    deliberately taken off the roster -- and `merge_duplicate_patients.py`
    archives the losing side of a merge, so matching them would hand back
    a record that has already been merged away.
    """
    suffix = _phone_suffix(phone or "")
    key = _same_person_key(name)
    if not suffix or not key:
        return None

    candidates = (
        db.query(models.Patient)
        .filter(
            models.Patient.org_id == org_id,
            models.Patient.status != "Archived",
            _phone_digits_sql(models.Patient.phone).like(f"%{suffix}"),
        )
        .all()
    )
    for candidate in candidates:
        if _same_person_key(candidate.name) == key:
            return candidate
    return None


def _phone_suffix(search: str) -> Optional[str]:
    """
    The part of a search term that identifies a phone line, or None if
    the term is not phone-shaped.

    WHY A SUFFIX AND NOT THE WHOLE NUMBER

    The same line is written several ways depending on where it came
    from. Twilio delivers the caller in E.164 -- "+918688460308" -- while
    a receptionist typing into the dashboard writes "8688460308", and an
    imported record might carry "(555) 220-4471". Comparing those as
    strings finds nothing, which is exactly what was happening: every
    real patient in this database is stored without a country code, so
    caller recognition matched nobody on any live call.

    Taking the last ten digits makes all three spellings the same
    question. Ten because that is the national subscriber length in both
    India and the US; a shorter term is used whole, down to
    `_MIN_PHONE_DIGITS`.
    """
    digits = _phone_digits(search)
    if len(digits) < _MIN_PHONE_DIGITS:
        return None
    return digits[-10:]


def _get_patient_or_404(db: Session, patient_id: str, org_id: str) -> models.Patient:
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id, models.Patient.org_id == org_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def _generate_mrn(db: Session, org_id: str) -> str:
    """Generates a unique MRN in the clinic's existing MRN-XXXXX-Y format."""
    for _ in range(10):
        candidate = f"MRN-{random.randint(10000, 99999)}-{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
        exists = (
            db.query(models.Patient)
            .filter(models.Patient.org_id == org_id, models.Patient.mrn == candidate)
            .first()
        )
        if not exists:
            return candidate
    raise HTTPException(status_code=500, detail="Could not generate a unique MRN, please retry")


@router.get("", response_model=list[schemas.PatientListOut])
def list_patients(
    search: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    include_archived: bool = False,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    `limit`/`offset` are optional (Phase 8, opt-in pagination for large
    orgs) — omitting `limit` preserves the previous behaviour of
    returning every matching patient.

    `include_archived` defaults to false so archiving behaves, from the
    roster's point of view, the way deleting used to.
    """
    q = db.query(models.Patient).filter(models.Patient.org_id == ctx.org_id)
    if not include_archived:
        # Archived patients stay in the database (and remain reachable by
        # id, and by an explicit search) but drop out of the default
        # roster the same way a deleted one used to.
        q = q.filter(models.Patient.status != "Archived")
    if search:
        like = f"%{search}%"
        clauses = [
            models.Patient.name.ilike(like),
            models.Patient.mrn.ilike(like),
            models.Patient.phone.ilike(like),
        ]

        # A phone-shaped term also matches on digits alone, so that the
        # way a number happens to be written stops mattering. This is
        # what makes caller recognition work: Twilio hands us
        # "+918688460308" and the record says "8688460308", and before
        # this those were different patients as far as the search was
        # concerned -- which is to say, no patient at all.
        #
        # Additive. Everything the previous three clauses matched, they
        # still match; this only widens, and only for terms that are
        # mostly digits.
        suffix = _phone_suffix(search)
        if suffix:
            clauses.append(_phone_digits_sql(models.Patient.phone).like(f"%{suffix}"))

        q = q.filter(or_(*clauses))
    q = q.order_by(models.Patient.name)
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(min(limit, 500))
    return q.all()


def _stamp_consent(
    patient: models.Patient, data: dict, actor: str, previous: Optional[str]
) -> None:
    """
    Record *when* consent was answered and *how* it reached us.

    The caller supplies the answer; these two are derived here rather than
    trusted from the request, for the same reason the backend decides
    which detected language becomes a stored preference: which signal
    becomes a record is a product rule, not something a client asserts.

    The source falls back to the actor, so a consent captured by the voice
    agent mid-call is distinguishable from one a receptionist typed --
    which is the distinction that matters if the answer is ever disputed.
    Only stamped when the answer actually changes, so re-saving a patient
    form does not keep moving the date on a consent given months ago. That
    date is evidence of a conversation, and a form save is not one.

    `previous` is passed in rather than read off `patient`, because on
    create the record has already been built from the same dict and the
    two would compare equal by construction -- which silently skipped the
    stamp on every newly registered patient.
    """
    if "contact_consent" not in data or data["contact_consent"] is None:
        return
    if data["contact_consent"] == previous:
        return
    patient.contact_consent_at = datetime.utcnow()
    if not data.get("contact_consent_source"):
        patient.contact_consent_source = "voice" if actor == "agent" else "staff"


@router.post("", response_model=schemas.PatientDetailOut)
def create_patient(
    payload: schemas.PatientCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    # Registering somebody who is already registered is not a new
    # patient. It is a lookup that went wrong.
    #
    # This is how the duplicates in this database were made: a returning
    # caller rang, the caller-id lookup failed to match them, the agent
    # concluded they were new, and registered them a second time. That
    # search is fixed (see `_phone_suffix`), but the prompt telling the
    # model to look somebody up before registering them is a request, not
    # a rule -- and a second medical record splits a patient's
    # appointments, prescriptions and call history between two rows.
    #
    # Checked BEFORE the plan limit below: an existing patient consumes
    # no new capacity, so a clinic at its cap should still be told that
    # this person is already on file rather than being sold an upgrade.
    existing = find_existing_patient(db, ctx.org_id, payload.name, payload.phone)
    if existing is not None:
        if ctx.actor == "user":
            # Staff are at a screen and can act. The modal surfaces this
            # message, so it is written for a receptionist rather than a
            # developer.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{existing.name} is already registered on that number "
                    f"(MRN {existing.mrn}). Open that record instead of "
                    "creating a second one."
                ),
            )

        # The agent is mid-conversation. Handing back the record it
        # should have found is the only answer that leaves the caller
        # with a working call AND the clinic with one record -- the same
        # reasoning as the plan limit below, which also refuses to fail a
        # patient on a live call over something they cannot fix.
        logger.info(
            "Registration for %r matched existing patient %s; returned it "
            "instead of creating a duplicate",
            existing.name, existing.id,
        )
        audit_service.record(
            db, ctx.org_id, "patient.viewed", ctx=ctx,
            target_type="patient", target_id=existing.id,
            detail="Registration matched an existing patient; no record created",
            request=request, commit=True,
        )
        return existing

    # HV-4: plan patient limits were stored and displayed but never
    # checked. Enforced for STAFF only — never for the voice agent.
    #
    # This endpoint is dual-auth: the agent calls it to register a caller
    # mid-conversation. A clinic that has hit its plan's patient cap has
    # a billing problem, and failing a patient on a live phone call is the
    # wrong way to raise it. Staff get the upgrade prompt at a moment when
    # someone can actually act on it.
    if ctx.actor == "user":
        limit_error = usage_service.check_patient_limit(db, ctx.org_id)
        if limit_error:
            raise HTTPException(status_code=402, detail=limit_error)

    data = payload.model_dump(exclude={"vitals"})
    if not data.get("mrn"):
        data["mrn"] = _generate_mrn(db, ctx.org_id)
    patient = models.Patient(org_id=ctx.org_id, **data)
    # A brand-new record holds no previous answer, so any consent supplied
    # here is by definition a change.
    _stamp_consent(patient, data, ctx.actor, previous=None)
    if payload.vitals:
        patient.vitals_bp = payload.vitals.bp
        patient.vitals_bp_trend = payload.vitals.bp_trend
        patient.vitals_hr = payload.vitals.hr
        patient.vitals_weight = payload.vitals.weight
        patient.vitals_recorded = payload.vitals.recorded
    db.add(patient)
    db.flush()
    audit_service.record(
        db, ctx.org_id, "patient.created", ctx=ctx,
        target_type="patient", target_id=patient.id,
        detail=f"MRN {patient.mrn}", request=request,
    )
    db.commit()
    db.refresh(patient)
    # Best-effort, non-blocking: never let EHR reachability affect patient
    # creation (e.g. a caller registering over the phone).
    ehr_service.schedule_patient_sync(background_tasks, patient.id, ctx.org_id, ctx.actor)
    return patient


@router.get("/{patient_id}", response_model=schemas.PatientDetailOut)
def get_patient(
    patient_id: str,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(get_org_context),
):
    """
    HV-1: this is the read that gets logged.

    `GET /patients` (the list) deliberately does not — a row per
    dashboard render would bury the individual-record reads that a
    breach investigation actually needs. The line is one identified
    record, opened by one actor.

    It costs an INSERT on a read path, including when the voice agent
    looks a caller up mid-call. That is the point: it is what produces a
    per-call PHI access trail without the agent knowing anything about
    audit logging. The 404 is raised first, so probing for a patient id
    that belongs to another clinic writes nothing.
    """
    patient = _get_patient_or_404(db, patient_id, ctx.org_id)
    audit_service.record(
        db, ctx.org_id, "patient.viewed", ctx=ctx,
        target_type="patient", target_id=patient.id,
        detail=f"MRN {patient.mrn}",
        status=audit_service.STATUS_LOGGED,
        request=request, commit=True,
    )
    return patient


@router.patch("/{patient_id}", response_model=schemas.PatientDetailOut)
def update_patient(
    patient_id: str,
    payload: schemas.PatientUpdate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    patient = _get_patient_or_404(db, patient_id, user.org_id)
    data = payload.model_dump(exclude_unset=True, exclude={"vitals"})
    # Field names before they are overwritten. Names only, never values —
    # see `audit_service.changed_fields` for why the log must not become a
    # second copy of the record.
    before = {k: getattr(patient, k, None) for k in data}
    _stamp_consent(patient, data, "user", previous=patient.contact_consent)
    for k, v in data.items():
        setattr(patient, k, v)
    if payload.vitals:
        v = payload.vitals
        if v.bp is not None:
            patient.vitals_bp = v.bp
        if v.bp_trend is not None:
            patient.vitals_bp_trend = v.bp_trend
        if v.hr is not None:
            patient.vitals_hr = v.hr
        if v.weight is not None:
            patient.vitals_weight = v.weight
        if v.recorded is not None:
            patient.vitals_recorded = v.recorded
    changed = audit_service.changed_fields(before, data)
    if payload.vitals:
        changed = ", ".join(filter(None, [changed, "vitals"]))
    audit_service.record(
        db, user.org_id, "patient.updated", user=user,
        target_type="patient", target_id=patient.id,
        detail=changed, request=request,
    )
    db.commit()
    db.refresh(patient)
    ehr_service.schedule_patient_sync(background_tasks, patient.id, user.org_id, "user")
    return patient


@router.delete("/{patient_id}", status_code=204)
def archive_patient(
    patient_id: str,
    request: Request,
    purge: bool = False,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """
    Archives a patient (soft delete). Their record, prescriptions, visit
    history and appointments are all retained.

    This used to be a hard delete, and `Patient.prescriptions`,
    `.interactions` and `.appointments` all cascade with
    delete-orphan — so removing a patient destroyed their entire medical
    record along with the appointment history other reports depend on.
    That is the wrong default for a clinical system and, with a
    seven-year retention policy configured on the org, very likely the
    wrong thing legally too.

    `purge=true` performs the old hard delete, for a genuine erasure
    request. It is admin-only and deliberately explicit: a mistyped URL
    should never be able to destroy a medical record.
    """
    patient = _get_patient_or_404(db, patient_id, user.org_id)

    if purge:
        if user.role != models.UserRole.admin:
            raise HTTPException(
                status_code=403,
                detail="Permanently deleting a patient record requires an admin account.",
            )
        # Recorded before the delete, so the entry survives the record
        # it describes — an erasure with no trace of the erasure is not
        # an audit trail. `who` is denormalised for the same reason.
        audit_service.record(
            db, user.org_id, "patient.archived", user=user,
            target_type="patient", target_id=patient.id,
            detail=f"Permanently deleted (purge) — MRN {patient.mrn}",
            request=request,
        )
        db.delete(patient)
        db.commit()
        return None

    patient.status = "Archived"
    audit_service.record(
        db, user.org_id, "patient.archived", user=user,
        target_type="patient", target_id=patient.id,
        detail=f"MRN {patient.mrn}", request=request,
    )
    db.commit()
    return None


@router.post("/{patient_id}/restore", response_model=schemas.PatientDetailOut)
def restore_patient(
    patient_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """Undoes an archive. Archiving is reversible precisely so that it can be the default."""
    patient = _get_patient_or_404(db, patient_id, user.org_id)
    patient.status = "Active"
    audit_service.record(
        db, user.org_id, "patient.restored", user=user,
        target_type="patient", target_id=patient.id, request=request,
    )
    db.commit()
    db.refresh(patient)
    return patient


@router.post("/{patient_id}/prescriptions", response_model=schemas.PrescriptionOut)
def add_prescription(
    patient_id: str,
    payload: schemas.PrescriptionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    _get_patient_or_404(db, patient_id, user.org_id)
    rx = models.Prescription(patient_id=patient_id, **payload.model_dump())
    db.add(rx)
    audit_service.record(
        db, user.org_id, "patient.record_changed", user=user,
        target_type="patient", target_id=patient_id,
        detail=f"Prescription added: {rx.name}", request=request,
    )
    db.commit()
    db.refresh(rx)
    return rx


@router.delete("/{patient_id}/prescriptions/{rx_id}", status_code=204)
def delete_prescription(
    patient_id: str,
    rx_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    _get_patient_or_404(db, patient_id, user.org_id)
    rx = (
        db.query(models.Prescription)
        .filter(models.Prescription.id == rx_id, models.Prescription.patient_id == patient_id)
        .first()
    )
    if rx:
        audit_service.record(
            db, user.org_id, "patient.record_changed", user=user,
            target_type="patient", target_id=patient_id,
            detail=f"Prescription deleted: {rx.name}", request=request,
        )
        db.delete(rx)
        db.commit()
    return None


@router.post("/{patient_id}/interactions", response_model=schemas.InteractionOut)
def add_interaction(
    patient_id: str,
    payload: schemas.InteractionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    _get_patient_or_404(db, patient_id, user.org_id)
    interaction = models.Interaction(patient_id=patient_id, **payload.model_dump())
    db.add(interaction)
    audit_service.record(
        db, user.org_id, "patient.record_changed", user=user,
        target_type="patient", target_id=patient_id,
        detail="Visit note added", request=request,
    )
    db.commit()
    db.refresh(interaction)
    return interaction