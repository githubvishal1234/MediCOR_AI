import logging
import os
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from .. import audit_service, models, rag_service, schemas, timezones, usage_service
from ..config import settings
from ..database import get_db
from ..deps import OrgContext, get_current_user, get_org_context, require_admin

logger = logging.getLogger("knowledge_base")

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


def _require_kb_plan(db: Session, org_id: str) -> None:
    """
    403s a staff write when the org's plan excludes the knowledge base
    (HV-4).

    Applied to the three writes that add to what the AI knows —
    documents, crawled sources, FAQs — and deliberately **not** to
    clinic-info or services, which every plan needs: an agent that
    cannot say what time the clinic opens is broken, not downgraded.

    Reads stay open, matching the QW-7 line (`tests/test_qw7_rbac.py`).
    `/ask` is not gated here either — it is dual-auth and the voice agent
    calls it mid-call, so `rag_service.answer_question` degrades to
    `source: "none"` instead. A caller must never hear about billing.
    """
    plan_error = usage_service.feature_error(db, org_id, "knowledge_base_access")
    if plan_error:
        raise HTTPException(status_code=403, detail=plan_error)


_STOPWORDS = {
    "a", "an", "the", "is", "are", "do", "does", "you", "your", "i", "my",
    "what", "when", "where", "how", "can", "of", "for", "to", "in", "on",
    "at", "and", "or", "it", "this", "that", "with", "have", "has", "about",
}


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Phase 1 (#9) — upload hardening.
#
# Knowledge-base documents are clinic reference material: handbooks,
# policies, price lists. They are never executables, archives, or scripts.
# Allowlist (not denylist) the handful of types that are legitimate, so a
# new dangerous type is refused by default rather than on a maintainer
# remembering to add it.
#
# Both checks matter: `content_type` is client-supplied and trivially
# forged, so the extension is checked too. Neither is a substitute for
# storing uploads outside the web root (already the case here) — they
# reduce the surface, they don't make an uploaded file safe to execute.
ALLOWED_UPLOAD_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/markdown",
    "text/csv",
}
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md", ".csv",
}

# Read granularity for the streaming size check below. 64 KiB is large
# enough to keep syscall overhead negligible and small enough that an
# oversized upload is rejected after buffering only that much.
_UPLOAD_CHUNK_BYTES = 64 * 1024


@router.get("/documents", response_model=list[schemas.KBDocumentOut])
def list_documents(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return (
        db.query(models.KBDocument)
        .filter(models.KBDocument.org_id == user.org_id)
        .order_by(models.KBDocument.updated_at.desc())
        .all()
    )


@router.post("/documents", response_model=schemas.KBDocumentOut)
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    # Phase 8: `file.filename` is attacker-controlled input — using it
    # directly in os.path.join (the pre-existing behaviour) allowed a
    # crafted filename (e.g. "../../etc/something") to write outside the
    # intended org directory. Keep only the basename, reject anything
    # that still resolves outside org_dir, and store under a
    # collision-proof generated name while preserving the original name
    # for display purposes (KBDocument.name).
    # Ahead of the extension/content-type allowlist below, so a clinic
    # without the feature is told that rather than sent to fix its file.
    _require_kb_plan(db, user.org_id)

    original_name = os.path.basename(file.filename or "").strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="A filename is required")

    org_dir = os.path.join(UPLOAD_DIR, user.org_id)
    os.makedirs(org_dir, exist_ok=True)

    _, ext = os.path.splitext(original_name)

    # Phase 1 (#9): reject by type BEFORE reading a single byte.
    if ext.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported file type. Allowed: "
                + ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
            ),
        )
    if file.content_type and file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported content type: {file.content_type}",
        )

    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.normpath(os.path.join(org_dir, stored_name))
    if not dest_path.startswith(os.path.normpath(org_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Phase 1 (#9): enforce the size cap WHILE streaming, not after.
    #
    # The previous order was `contents = await file.read()` followed by a
    # length check — so an oversized upload was fully resident in memory
    # before being rejected, and a handful of concurrent multi-GB posts
    # could exhaust the process. Reading in bounded chunks and aborting
    # the moment the limit is crossed caps the damage at one chunk.
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    "File exceeds the maximum upload size of "
                    f"{settings.max_upload_bytes // (1024 * 1024)} MB"
                ),
            )
        chunks.append(chunk)
    contents = b"".join(chunks)

    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")

    try:
        with open(dest_path, "wb") as f:
            f.write(contents)
    except OSError:
        logger.exception(f"Failed to write uploaded document for org={user.org_id}")
        raise HTTPException(status_code=500, detail="Could not save the uploaded file")

    doc = models.KBDocument(
        org_id=user.org_id,
        name=original_name,
        size_bytes=len(contents),
        status="Indexing",
        file_path=dest_path,
    )
    db.add(doc)
    db.flush()
    audit_service.record(
        db, user.org_id, "knowledge_base.changed", user=user,
        target_type="kb_document", target_id=doc.id,
        detail=f"Document uploaded: {original_name} ({len(contents)} bytes)",
        request=request,
    )
    db.commit()
    db.refresh(doc)

    # AI-3: real indexing — parse, chunk, embed, store — in the
    # background. This used to flip straight to "Indexed" without ever
    # opening the file, so the dashboard reported a working knowledge
    # base that contained nothing. The document now stays "Indexing"
    # until the task finishes and becomes "Failed" if it cannot be read.
    rag_service.schedule_index(background_tasks, doc.id, user.org_id)
    return doc


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(
    doc_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    doc = (
        db.query(models.KBDocument)
        .filter(models.KBDocument.id == doc_id, models.KBDocument.org_id == user.org_id)
        .first()
    )
    if doc:
        if doc.file_path and os.path.exists(doc.file_path):
            os.remove(doc.file_path)
        audit_service.record(
            db, user.org_id, "knowledge_base.changed", user=user,
            target_type="kb_document", target_id=doc.id,
            detail=f"Document deleted: {doc.name}", request=request,
        )
        db.delete(doc)
        db.commit()
    return None


@router.get("/sources", response_model=list[schemas.KBSourceOut])
def list_sources(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return db.query(models.KBSource).filter(models.KBSource.org_id == user.org_id).all()


@router.post("/sources", response_model=schemas.KBSourceOut)
def add_source(
    payload: schemas.KBSourceBase,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    _require_kb_plan(db, user.org_id)
    source = models.KBSource(org_id=user.org_id, url=payload.url, status="Indexing")
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(
    source_id: str, db: Session = Depends(get_db), user: models.User = Depends(require_admin)
):
    source = (
        db.query(models.KBSource)
        .filter(models.KBSource.id == source_id, models.KBSource.org_id == user.org_id)
        .first()
    )
    if source:
        db.delete(source)
        db.commit()
    return None


@router.get("/faqs", response_model=list[schemas.FAQOut])
def list_faqs(db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)):
    """
    Dual-auth (dashboard JWT or voice-agent API key) so the voice agent can
    browse the raw FAQ list when needed, in addition to /knowledge-base/ask.
    """
    return db.query(models.FAQ).filter(models.FAQ.org_id == ctx.org_id).all()


@router.post("/faqs", response_model=schemas.FAQOut)
def add_faq(
    payload: schemas.FAQBase,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    _require_kb_plan(db, user.org_id)
    faq = models.FAQ(org_id=user.org_id, **payload.model_dump())
    db.add(faq)
    db.commit()
    db.refresh(faq)
    return faq


@router.delete("/faqs/{faq_id}", status_code=204)
def delete_faq(
    faq_id: str, db: Session = Depends(get_db), user: models.User = Depends(require_admin)
):
    faq = db.query(models.FAQ).filter(models.FAQ.id == faq_id, models.FAQ.org_id == user.org_id).first()
    if faq:
        db.delete(faq)
        db.commit()
    return None


@router.get("/search", response_model=dict)
def search_knowledge_base(
    q: str, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    like = f"%{q}%"
    docs = (
        db.query(models.KBDocument)
        .filter(models.KBDocument.org_id == user.org_id, models.KBDocument.name.ilike(like))
        .all()
    )
    faqs = (
        db.query(models.FAQ)
        .filter(
            models.FAQ.org_id == user.org_id,
            (models.FAQ.question.ilike(like)) | (models.FAQ.answer.ilike(like)),
        )
        .all()
    )
    return {
        "documents": [schemas.KBDocumentOut.model_validate(d) for d in docs],
        "faqs": [schemas.FAQOut.model_validate(f) for f in faqs],
    }


# ---------- Clinic info (timings, contact, general info) ----------
def _get_or_create_clinic_info(db: Session, org_id: str) -> models.ClinicInfo:
    info = db.query(models.ClinicInfo).filter(models.ClinicInfo.org_id == org_id).first()
    if not info:
        info = models.ClinicInfo(org_id=org_id)
        db.add(info)
        db.commit()
        db.refresh(info)
    return info


def _clinic_info_out(info: models.ClinicInfo, org: models.Organization) -> schemas.ClinicInfoOut:
    """
    Merges the ClinicInfo row with the org's timezone.

    Timezone lives on Organization (it governs scheduling, not just what
    the agent says), but it belongs in the same clinic-profile form, so
    it is presented here rather than forcing a second screen.
    """
    tz_name = timezones.org_timezone_name(org)
    return schemas.ClinicInfoOut(
        timezone=tz_name,
        timezone_label=timezones.utc_offset_label(timezones.resolve(tz_name)),
        timings_text=info.timings_text,
        address=info.address,
        phone=info.phone,
        email=info.email,
        website=info.website,
        general_info=info.general_info,
    )


@router.get("/timezones", response_model=list[str])
def list_timezones(user: models.User = Depends(get_current_user)):
    """
    Every IANA zone this deployment can resolve, for the profile picker.

    An empty list here means the `tzdata` package is missing (Windows
    ships no system timezone database), which would silently pin every
    clinic to UTC — see app/timezones.py.
    """
    return list(timezones.all_timezone_names())


@router.get("/clinic-info", response_model=schemas.ClinicInfoOut)
def get_clinic_info(db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)):
    """
    Structured clinic facts (timings, address, phone, email, website,
    general info). Dual-auth: the dashboard uses this to
    display/edit the profile, the voice agent uses it to answer callers
    instantly without a fuzzy FAQ search.
    """
    info = _get_or_create_clinic_info(db, ctx.org_id)
    org = db.query(models.Organization).filter(models.Organization.id == ctx.org_id).first()
    return _clinic_info_out(info, org)


@router.put("/clinic-info", response_model=schemas.ClinicInfoOut)
def update_clinic_info(
    payload: schemas.ClinicInfoUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """Admin/staff-only (JWT) — updates the clinic profile from the dashboard."""
    info = _get_or_create_clinic_info(db, user.org_id)
    org = db.query(models.Organization).filter(models.Organization.id == user.org_id).first()
    data = payload.model_dump(exclude_unset=True)

    # Timezone is stored on the org and changes how every future booking
    # is interpreted, so reject an unusable value loudly rather than
    # silently falling back to UTC and quietly shifting the slot grid.
    if "timezone" in data:
        tz_name = (data.pop("timezone") or "").strip()
        if tz_name:
            if not timezones.is_valid(tz_name):
                raise HTTPException(
                    status_code=422,
                    detail=f"'{tz_name}' is not a recognised timezone name. Use an IANA name such as 'Asia/Kolkata'.",
                )
            if org is not None:
                org.timezone = tz_name

    before = {k: getattr(info, k, None) for k in data}
    for k, v in data.items():
        setattr(info, k, v)
    info.updated_at = datetime.utcnow()
    audit_service.record(
        db, user.org_id, "clinic_info.updated", user=user,
        target_type="clinic_info", target_id=info.id,
        detail=audit_service.changed_fields(before, data), request=request,
    )
    db.commit()
    db.refresh(info)
    if org is not None:
        db.refresh(org)
    return _clinic_info_out(info, org)


# ---------- Services & consultation fees ----------
@router.get("/services", response_model=list[schemas.ServiceOut])
def list_services(db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)):
    """Dual-auth: lists active clinic services (and their fees) for dashboard and voice agent."""
    return (
        db.query(models.Service)
        .filter(models.Service.org_id == ctx.org_id, models.Service.active == True)  # noqa: E712 - SQL Server BIT compatibility
        .order_by(models.Service.name)
        .all()
    )


@router.post("/services", response_model=schemas.ServiceOut)
def create_service(
    payload: schemas.ServiceBase,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    service = models.Service(org_id=user.org_id, **payload.model_dump())
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


@router.patch("/services/{service_id}", response_model=schemas.ServiceOut)
def update_service(
    service_id: str,
    payload: schemas.ServiceUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """Edit a service in place — previously create/delete only."""
    service = (
        db.query(models.Service)
        .filter(models.Service.id == service_id, models.Service.org_id == user.org_id)
        .first()
    )
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(service, k, v)
    db.commit()
    db.refresh(service)
    return service


@router.delete("/services/{service_id}", status_code=204)
def delete_service(
    service_id: str, db: Session = Depends(get_db), user: models.User = Depends(require_admin)
):
    service = (
        db.query(models.Service)
        .filter(models.Service.id == service_id, models.Service.org_id == user.org_id)
        .first()
    )
    if service:
        db.delete(service)
        db.commit()
    return None


# ---------- Ask (general Q&A over FAQs, with a clinic-info-aware fallback) ----------
@router.get("/ask", response_model=schemas.KBAnswerOut)
def ask_knowledge_base(
    q: str, db: Session = Depends(get_db), ctx: OrgContext = Depends(get_org_context)
):
    """
    Answers a freeform question from the org's knowledge base (AI-3).

    Retrieval-augmented: the question is embedded locally, matched
    against indexed document chunks and FAQs by cosine similarity, and
    answered ONLY from text that scored above `kb_min_similarity`.

    This replaces bag-of-words overlap with `best_score >= 1`, under
    which a single shared keyword returned an FAQ as an authoritative
    answer to a patient on a live call. Anything below the threshold now
    returns `source: "none"`, which the voice agent already treats as
    "I don't have that — offer a transfer".

    The response shape is unchanged, so `voice-agent/` needed no edit.
    """
    return schemas.KBAnswerOut(**rag_service.answer_question(db, ctx.org_id, q))
