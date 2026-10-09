import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from . import deployment, instance, jobs, models, scheduler
from .config import settings
from .database import Base, SessionLocal, engine, sync_missing_columns
from .exceptions import register_exception_handlers
from .logging_config import configure_logging
from .middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from .realtime import manager as realtime_manager
from .routers import (
    admin,
    analytics,
    admin_plans,
    admin_usage,
    agent_runtime,
    agent_settings,
    appointment_link,
    appointments,
    auth,
    billing,
    calls,
    dashboard,
    doctors,
    ehr,
    inbound_messages,
    knowledge_base,
    notifications,
    onboarding,
    patients,
    payment_webhook,
    public_catalog,
    quality,
    refills,
    security_compliance,
    support,
    users,
    waiting_list,
    ws,
)

configure_logging()
logger = logging.getLogger("startup")

# Everything about how this process was *deployed* rather than what it
# does: the secret key, the origins the dashboard is served from, the URL
# Twilio signs against, and whether a proxy is trusted. Each is correct in
# development and wrong in production, and each fails in a way nobody can
# see from the outside -- so they are checked here, together, and every
# problem is reported before any of them raises. See app/deployment.py.
deployment.check(settings, logger)

# GAP-T1. Two pieces of this app hold state in the process rather than
# the database -- rate-limit counters and dashboard WebSockets -- so a
# second worker silently weakens an authentication control and drops live
# events. Neither failure raises anything or looks broken on screen, which
# is why this is checked here rather than left to be noticed. Fatal in
# production for the same reason the SECRET_KEY check is; see
# app/instance.py for the full argument and the escape hatch.
instance.check_worker_configuration(
    is_production=settings.is_production,
    allow_override=settings.allow_multiple_workers,
)

if settings.auto_create_schema:
    # Legacy bootstrap, retained as a safety net while Alembic is adopted.
    # Both calls are idempotent: create_all() only creates missing tables,
    # sync_missing_columns() only adds missing nullable columns. Set
    # AUTO_CREATE_SCHEMA=false once every environment is on Alembic —
    # see alembic/README.md.
    Base.metadata.create_all(bind=engine)
    sync_missing_columns()
else:
    logger.info("AUTO_CREATE_SCHEMA=false — schema is managed by Alembic only.")

app = FastAPI(
    title="MediCOR AI API",
    version="1.0.0",
    # In production, don't expose interactive API docs / schema publicly
    # by default. Dev/local behaviour (docs enabled) is unchanged.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Order matters: outermost-added middleware runs first on the way in /
# last on the way out. Security headers + request logging should wrap
# everything, including CORS handling and error responses.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    expose_headers=["X-Request-ID"],
)

register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(patients.router)
app.include_router(doctors.router)
app.include_router(calls.router)
app.include_router(appointments.router)
# Public and unauthenticated by design -- one appointment, one token.
# See routers/appointment_link.py for the boundary it draws.
app.include_router(waiting_list.router)
app.include_router(appointment_link.router)
# Also public by design: Twilio calls it, not a user. Every request's
# signature is verified before anything is read.
app.include_router(inbound_messages.router)
# Public by design, and the only unauthenticated router that touches no
# tenant at all: the plan catalogue the pricing page reads instead of
# asserting prices of its own.
app.include_router(public_catalog.router)
# Public by design and signature-verified: Razorpay posts here.
# This is the AUTHORITATIVE payment path -- the browser callback in
# routers/billing.py never arrives if the clinic closes the tab
# mid-payment, and they would be charged without being upgraded.
app.include_router(payment_webhook.router)
app.include_router(knowledge_base.router)
app.include_router(agent_settings.router)
app.include_router(agent_runtime.router)
app.include_router(ehr.router)
app.include_router(security_compliance.router)
app.include_router(billing.router)
app.include_router(support.router)
app.include_router(users.router)
app.include_router(notifications.router)
app.include_router(quality.router)
app.include_router(refills.router)
app.include_router(analytics.router)
app.include_router(onboarding.router)
app.include_router(ws.router)
app.include_router(admin.router)
app.include_router(admin_plans.router)
app.include_router(admin_usage.router)


@app.on_event("startup")
async def _bind_realtime_loop():
    # Lets synchronous request handlers (the whole rest of this app) push
    # WebSocket events without becoming async themselves. See app/realtime.py.
    realtime_manager.bind_loop(asyncio.get_running_loop())
    logger.info(f"MediCOR AI API started (environment={settings.environment})")


@app.on_event("startup")
async def _start_scheduler():
    """
    Time-triggered work. Until this existed nothing could happen on a
    schedule at all — every background task was a `BackgroundTasks`
    callback, which only runs because a request arrived.

    Safe to start in every process: jobs are claimed through a UNIQUE
    row in `scheduled_job_runs`, so a second worker's scheduler finds
    every bucket already taken and does nothing. See app/scheduler.py.
    """
    jobs.register_all()
    scheduler.start()


@app.on_event("shutdown")
async def _stop_scheduler():
    await scheduler.stop()


@app.on_event("startup")
def _fix_stale_demo_live_calls():
    # Phase 1 (#8): this one-time correction for demo rows seeded by an
    # older app/seed.py no longer runs in production.
    #
    # It was always idempotent and never touched real call history, but a
    # production API should not carry a startup path that looks up a
    # hardcoded demo organization by name and mutates rows. Any
    # environment that still has those rows can correct them once by
    # running with ENVIRONMENT=development, or by calling
    # seed._reconcile_stale_demo_calls directly.
    if settings.is_production:
        return

    from .seed import _reconcile_stale_demo_calls

    db = SessionLocal()
    try:
        org = db.query(models.Organization).filter(models.Organization.name == "HealthLink Clinic").first()
        _reconcile_stale_demo_calls(db, org)
    except Exception:
        logger.exception("Failed to reconcile stale demo call records")
    finally:
        db.close()


@app.get("/health")
def health():
    """
    Liveness, plus the one deployment constraint this app cannot enforce
    from the inside (GAP-T1).

    `status` deliberately stays "ok" when workers are wrong. A monitoring
    system that pulls this must be able to alert on the misconfiguration
    without a load balancer reading it as "unhealthy" and pulling the only
    process out of rotation -- which would turn a degraded deployment into
    an outage. The numbers are here to be alerted on; the verdict is not.

    `peers` is other instances observed via the scheduler claim, so it is
    0 until a tick has actually raced. `configured` catches the same
    problem at startup instead. Neither subsumes the other.
    """
    return {"status": "ok", "workers": instance.status()}