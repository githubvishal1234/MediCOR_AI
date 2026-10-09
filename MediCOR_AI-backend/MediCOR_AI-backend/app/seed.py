"""
Seeds the database with demo data mirroring src/lib/data.js from the frontend,
so the app looks populated immediately after `npm run dev` is pointed at this API.

Usage: python -m app.seed
"""
from datetime import datetime, timedelta

from . import models
from .database import Base, SessionLocal, engine
from .security import hash_password

Base.metadata.create_all(bind=engine)

# Demo call records inserted by earlier versions of this script (before this
# fix) that were never given an explicit `status`, so they defaulted to
# "in_progress" and never got an `ended_at` — making them appear forever as
# "Live Active Calls" on the dashboard even though they're finished demo
# records. `_reconcile_stale_demo_calls` corrects any such rows still sitting
# in an already-seeded database. It is idempotent and only touches rows that
# match this known demo set with status still "in_progress" — no real call
# history is altered.
_DEMO_CALL_PATIENT_NAMES = {"Sarah Jenkins", "Michael Chen", "Unknown Caller", "Elena Rodriguez"}


# The demo clinic's services. Seeded through `_backfill_demo_services` rather
# than inline with the rest of the org, because the original seed created no
# services at all — so every already-seeded database has an empty Services tab
# and a voice agent that cannot answer "what do you charge for a consultation?".
# Backfilling by name means re-running this script fixes those without
# duplicating anything or touching a service a clinic added itself.
_DEMO_SERVICES = (
    ("General Consultation", "Standard appointment with a GP, including basic examination.", "$80"),
    ("Follow-up Visit", "Review appointment within 30 days of a related consultation.", "$50"),
    ("Annual Health Check", "Full physical, bloodwork and a written summary. Allow 45 minutes.", "$220"),
    ("Vaccination", "Routine and travel vaccinations. Fee varies by vaccine.", "From $35"),
    ("Blood Test & Lab Work", "On-site collection; results usually within two business days.", "From $45"),
)


# The demo clinic's own facts. Like the services above, the original seed
# created no ClinicInfo row at all — the voice agent reads these fields to
# answer the most common inbound question there is ("what time do you
# open?"), so an unseeded demo org has an agent that says "I don't have
# that specific detail" to it.
#
# `timings_text` is free text on purpose: the agent reads it aloud, and a
# structured week grid would have to be re-rendered back into a sentence.
_DEMO_CLINIC_INFO = {
    "timings_text": (
        "Monday to Friday, 8:00 AM to 6:00 PM. "
        "Saturday, 9:00 AM to 1:00 PM. Closed Sundays and public holidays."
    ),
    "address": "412 Harborview Avenue, Suite 200, Springfield, IL 62704",
    "phone": "(555) 014-2200",
    "email": "reception@healthlinkclinic.com",
    "website": "healthlinkclinic.com",
    "general_info": (
        "HealthLink Clinic is a family practice with two GPs. Free on-site parking is "
        "available at the rear of the building, and the entrance is step-free. "
        "Please arrive ten minutes early for a first visit."
    ),
}


def _backfill_demo_clinic_info(db, org):
    """
    Fills in the demo clinic's own facts, without overwriting real edits.

    Only blank fields are set, so a clinic (or a demo run) that has edited
    its opening hours keeps them. The row itself is created if missing —
    `_get_or_create_clinic_info` in the KB router otherwise creates an
    empty one on first read, which is what leaves the Clinic Profile page
    looking blank.
    """
    if org is None:
        return
    info = (
        db.query(models.ClinicInfo).filter(models.ClinicInfo.org_id == org.id).first()
    )
    if info is None:
        info = models.ClinicInfo(org_id=org.id)
        db.add(info)
    filled = []
    for field, value in _DEMO_CLINIC_INFO.items():
        if not (getattr(info, field) or "").strip():
            setattr(info, field, value)
            filled.append(field)
    if not filled:
        return
    info.updated_at = datetime.utcnow()
    db.commit()
    print(f"Seeded clinic info ({len(filled)} field(s): {', '.join(filled)}).")


def _backfill_demo_services(db, org):
    """
    Adds any of `_DEMO_SERVICES` the demo org does not already have.

    Idempotent and additive: matched by name, so nothing is duplicated and
    nothing already present is modified.
    """
    if org is None:
        return
    existing = {
        name for (name,) in db.query(models.Service.name).filter(models.Service.org_id == org.id)
    }
    missing = [s for s in _DEMO_SERVICES if s[0] not in existing]
    if not missing:
        return
    db.add_all([
        models.Service(org_id=org.id, name=name, description=description, fee_label=fee)
        for name, description, fee in missing
    ])
    db.commit()
    print(f"Seeded {len(missing)} clinic service(s).")


def _next_working_slot(hour: int, days_ahead: int = 1) -> datetime:
    """
    A clinic-local wall-clock datetime `days_ahead` days from now at
    `hour`:00, rolled forward to the next Mon-Fri if it lands on a weekend.

    The demo org is seeded on UTC, so utcnow() *is* its wall clock here.
    See app/timezones.py for the storage convention.
    """
    target = (datetime.utcnow() + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    while target.weekday() > 4:  # Sat=5, Sun=6
        target += timedelta(days=1)
    return target


def _slot_label(dt: datetime) -> str:
    """Human label matching routers/appointments.py's own formatting."""
    return f"{dt.strftime('%a, %b')} {dt.day}, {dt.strftime('%I:%M %p').lstrip('0')}"


def _reconcile_stale_demo_calls(db, org):
    if org is None:
        return
    stale = (
        db.query(models.CallLog)
        .filter(
            models.CallLog.org_id == org.id,
            models.CallLog.status == "in_progress",
            models.CallLog.patient_name.in_(_DEMO_CALL_PATIENT_NAMES),
        )
        .all()
    )
    if not stale:
        return
    for call in stale:
        started = call.started_at or call.occurred_at or datetime.utcnow()
        call.started_at = call.started_at or started
        call.status = "completed"
        call.ended_at = call.ended_at or (started + timedelta(minutes=3))
    db.commit()
    print(f"Reconciled {len(stale)} stale demo call record(s) to status=completed.")


# Default Phase 5 Plans catalog. Seeded independently of the demo org so
# that running this script on an already-seeded database (the common case
# for anyone who seeded before the Plans catalog existed) still populates
# it — otherwise the Super Admin "Assign plan" dropdown on the Organization
# detail page has nothing to show. Matched by unique `name`, so re-running
# this is a no-op once the plans exist.
_DEFAULT_PLANS = [
    dict(
        name="Starter",
        description="For small clinics getting started with the voice agent.",
        monthly_price_cents=9900,
        voice_minutes_limit=1000,
        user_limit=5,
        patient_limit=250,
        ehr_access=False,
        knowledge_base_access=True,
        features_csv="voice_agent,knowledge_base",
    ),
    dict(
        name="Growth",
        description="For growing practices that need EHR access and more headroom.",
        monthly_price_cents=29900,
        voice_minutes_limit=5000,
        user_limit=20,
        patient_limit=2000,
        ehr_access=True,
        knowledge_base_access=True,
        features_csv="voice_agent,knowledge_base,ehr_access",
    ),
    dict(
        name="Enterprise",
        description="For large, multi-provider clinics with high call volume.",
        monthly_price_cents=79900,
        voice_minutes_limit=15000,
        user_limit=None,
        patient_limit=None,
        ehr_access=True,
        knowledge_base_access=True,
        features_csv="voice_agent,knowledge_base,ehr_access,priority_support",
    ),
]


def _seed_default_plans(db):
    existing_names = {name for (name,) in db.query(models.Plan.name).all()}
    created = 0
    for plan_kwargs in _DEFAULT_PLANS:
        if plan_kwargs["name"] in existing_names:
            continue
        db.add(models.Plan(**plan_kwargs))
        created += 1
    if created:
        db.commit()
        print(f"Seeded {created} default plan(s) into the Plans catalog.")


def run():
    db = SessionLocal()
    try:
        _seed_default_plans(db)

        existing = db.query(models.Organization).filter(models.Organization.name == "HealthLink Clinic").first()
        if existing:
            _reconcile_stale_demo_calls(db, existing)
            _backfill_demo_clinic_info(db, existing)
            _backfill_demo_services(db, existing)
            print("Demo org already seeded — skipping.")
            return

        org = models.Organization(
            name="HealthLink Clinic",
            timezone="UTC",
            plan="Professional Plan",
            voice_minutes_used=3450,
            voice_minutes_limit=5000,
            next_billing_date=datetime.utcnow() + timedelta(days=14),
            hipaa_last_audit="Oct 12, 2023",
        )
        db.add(org)
        db.flush()

        admin = models.User(
            org_id=org.id,
            email="admin@healthlinkclinic.com",
            hashed_password=hash_password("password123"),
            full_name="Admin",
            role=models.UserRole.admin,
        )
        db.add(admin)
        for i in range(1, 13):
            db.add(
                models.User(
                    org_id=org.id,
                    email=f"staff{i}@healthlinkclinic.com",
                    hashed_password=hash_password("password123"),
                    full_name=f"Staff Member {i}",
                    role=models.UserRole.medical_staff,
                )
            )
        db.add(
            models.User(
                org_id=org.id,
                email="voice-agent@healthlinkclinic.com",
                hashed_password=hash_password("password123"),
                full_name="MediCOR Agent",
                role=models.UserRole.ai_agent,
            )
        )

        db.add(models.AgentSettings(org_id=org.id, voice_profile="Dr. Sarah (Calm, Professional)"))
        db.add(models.Webhook(org_id=org.id))

        # --- doctors (for scheduling) ---
        # Bound to names (rather than inlined in add_all) so the demo
        # pending bookings further down can reference a real doctor_id —
        # verifying a pending booking now builds a fully scheduled
        # appointment and validates the slot, so a doctor is required.
        dr_chen = models.Doctor(
            org_id=org.id,
            name="Dr. Robert Chen",
            specialty="Cardiology / Primary Care",
            slot_minutes=30,
            work_start_hour=9,
            work_end_hour=17,
            work_days_csv="0,1,2,3,4",
        )
        dr_foster = models.Doctor(
            org_id=org.id,
            name="Dr. Amanda Foster",
            specialty="Family Medicine",
            slot_minutes=20,
            work_start_hour=8,
            work_end_hour=16,
            work_days_csv="0,1,2,3,4",
        )
        db.add_all([dr_chen, dr_foster])
        db.flush()

        # --- patients ---
        sarah = models.Patient(
            org_id=org.id,
            mrn="MRN-94827-X",
            name="Sarah Jenkins",
            dob="Oct 12, 1945",
            age=78,
            phone="(555) 867-5309",
            doctor="Dr. Robert Chen (Primary)",
            status="Active",
            initials="SJ",
            vitals_bp="128/82",
            vitals_bp_trend="down",
            vitals_hr="72 bpm",
            vitals_weight="142 lbs",
            vitals_recorded="Oct 10, 2023",
        )
        db.add(sarah)
        db.flush()
        db.add_all(
            [
                models.Prescription(
                    patient_id=sarah.id,
                    name="Lisinopril 10mg",
                    detail="1 tablet daily (Hypertension)",
                    status="Refill Soon",
                    note="7 days left",
                ),
                models.Prescription(
                    patient_id=sarah.id,
                    name="Atorvastatin 20mg",
                    detail="1 tablet at bedtime (Cholesterol)",
                    status="Active",
                    note="45 days left",
                ),
            ]
        )
        db.add_all(
            [
                models.Interaction(
                    patient_id=sarah.id,
                    title="Routine Check-in",
                    date_label="Yesterday, 10:30 AM",
                    detail="Patient reported mild dizziness when standing. AI Agent successfully scheduled follow-up for next Tuesday.",
                    has_audio=True,
                    duration="2:14",
                ),
                models.Interaction(
                    patient_id=sarah.id,
                    title="Medication Reminder",
                    date_label="Oct 15, 2023",
                    detail="Completed successfully",
                ),
                models.Interaction(
                    patient_id=sarah.id,
                    title="Appointment Confirmation",
                    date_label="Oct 02, 2023",
                    detail="Rescheduled — patient requested new time",
                ),
            ]
        )
        db.add_all(
            [
                models.Appointment(
                    org_id=org.id,
                    patient_id=sarah.id,
                    title="Cardiology Follow-up",
                    patient_name=sarah.name,
                    time_label="Next Tue, Oct 24 · 2:00 PM",
                    location="Main Clinic, Room 3B",
                    status="upcoming",
                    ai_generated=True,
                ),
                models.Appointment(
                    org_id=org.id,
                    patient_id=sarah.id,
                    title="Annual Physical",
                    patient_name=sarah.name,
                    time_label="Oct 10, 2023",
                    location="Completed",
                    status="done",
                ),
            ]
        )

        michael = models.Patient(
            org_id=org.id,
            mrn="MRN-70213-B",
            name="Michael Chen",
            dob="Mar 3, 1978",
            age=47,
            phone="(555) 220-4471",
            doctor="Dr. Robert Chen (Primary)",
            status="Active",
            initials="MC",
            vitals_bp="118/76",
            vitals_bp_trend="flat",
            vitals_hr="68 bpm",
            vitals_weight="178 lbs",
            vitals_recorded="Oct 8, 2023",
        )
        db.add(michael)
        db.flush()
        db.add(
            models.Prescription(
                patient_id=michael.id,
                name="Amoxicillin 500mg",
                detail="3x daily (Post-op infection)",
                status="Active",
                note="3 days left",
            )
        )
        db.add(
            models.Interaction(
                patient_id=michael.id,
                title="Post-op Symptoms",
                date_label="Today, 09:15 AM",
                detail="Reported mild swelling. Transferred to on-call nurse for review.",
                has_audio=True,
                duration="5:30",
            )
        )
        db.add(
            models.Appointment(
                org_id=org.id,
                patient_id=michael.id,
                title="Post-op Review",
                patient_name=michael.name,
                time_label="Fri, Oct 27 · 11:00 AM",
                location="Main Clinic, Room 2A",
                status="upcoming",
            )
        )

        # --- call logs + transcript ---
        call1 = models.CallLog(
            org_id=org.id,
            patient_id=sarah.id,
            patient_name="Sarah Jenkins",
            timestamp_label="Today, 09:42 AM",
            occurred_at=datetime.utcnow() - timedelta(hours=1),
            started_at=datetime.utcnow() - timedelta(hours=1),
            ended_at=datetime.utcnow() - timedelta(hours=1) + timedelta(minutes=2, seconds=14),
            status="completed",
            reason="Rescheduling",
            duration="2m 14s",
            duration_seconds=134,
            outcome="Booked",
            sentiment="Positive",
            ai_summary=(
                "Patient called to reschedule her follow-up appointment with Dr. Smith originally "
                "set for tomorrow. She is experiencing mild transportation issues. Successfully "
                "moved appointment to next Tuesday at 2:00 PM. No urgent symptoms reported."
            ),
            actions_taken="Updated EHR schedule (Automated)\nSent confirmation SMS to patient",
        )
        db.add(call1)
        db.flush()
        db.add_all(
            [
                models.TranscriptMessage(
                    call_id=call1.id,
                    who="ai",
                    text="Hello, thank you for calling HealthLink Clinic. I'm MediCOR, the AI assistant. How can I help you today?",
                    time_label="00:02",
                ),
                models.TranscriptMessage(
                    call_id=call1.id,
                    who="patient",
                    text="Hi, this is Sarah Jenkins. I need to reschedule my appointment for tomorrow. My car broke down.",
                    time_label="00:10",
                ),
                models.TranscriptMessage(
                    call_id=call1.id,
                    who="ai",
                    text="I can help with that, Sarah. I see you have a follow-up with Dr. Smith tomorrow at 10:00 AM. I have openings next Tuesday at 2:00 PM or Thursday at 9:00 AM. Would either of those work for you?",
                    time_label="00:18",
                ),
                models.TranscriptMessage(
                    call_id=call1.id, who="patient", text="Tuesday at 2:00 PM would be perfect.", time_label="00:32"
                ),
            ]
        )
        db.add(
            models.CallLog(
                org_id=org.id,
                patient_id=michael.id,
                patient_name="Michael Chen",
                timestamp_label="Today, 09:15 AM",
                occurred_at=datetime.utcnow() - timedelta(hours=1, minutes=30),
                started_at=datetime.utcnow() - timedelta(hours=1, minutes=30),
                ended_at=datetime.utcnow() - timedelta(hours=1, minutes=30) + timedelta(minutes=5, seconds=30),
                status="completed",
                reason="Post-op Symptoms",
                duration="5m 30s",
                duration_seconds=330,
                outcome="Transferred to Nurse",
                sentiment="Concerned",
            )
        )
        db.add(
            models.CallLog(
                org_id=org.id,
                patient_name="Unknown Caller",
                timestamp_label="Today, 08:50 AM",
                occurred_at=datetime.utcnow() - timedelta(hours=2),
                started_at=datetime.utcnow() - timedelta(hours=2),
                ended_at=datetime.utcnow() - timedelta(hours=2) + timedelta(seconds=45),
                status="completed",
                reason="Clinic Hours",
                duration="0m 45s",
                duration_seconds=45,
                outcome="FAQ Answered",
                sentiment="Neutral",
            )
        )
        db.add(
            models.CallLog(
                org_id=org.id,
                patient_name="Elena Rodriguez",
                timestamp_label="Yesterday, 04:20 PM",
                occurred_at=datetime.utcnow() - timedelta(days=1),
                started_at=datetime.utcnow() - timedelta(days=1),
                ended_at=datetime.utcnow() - timedelta(days=1) + timedelta(minutes=4, seconds=10),
                status="completed",
                reason="New Patient",
                duration="4m 10s",
                duration_seconds=250,
                outcome="Booked",
                sentiment="Positive",
            )
        )

        # --- more appointments for the week grid ---
        db.add_all(
            [
                models.Appointment(org_id=org.id, title="Dr. Smith – Consult", patient_name="Sarah Jenkins", day_label="Mon 12", time_label="8:00 AM", ai_generated=True),
                models.Appointment(org_id=org.id, title="Dr. Allen – Follow Up", patient_name="Michael Chang", day_label="Tue 13", time_label="9:00 AM"),
                models.Appointment(org_id=org.id, title="Dr. Smith – New Patient", patient_name="Emily Roberts", day_label="Tue 13", time_label="9:00 AM", ai_generated=True),
                models.Appointment(org_id=org.id, title="Dr. Allen – Consult", patient_name="David Miller", day_label="Wed 14", time_label="10:00 AM"),
            ]
        )

        # --- pending bookings ---
        # Each carries a real doctor + a concrete future slot inside that
        # doctor's working hours, so "Verify" produces an appointment that
        # actually lands on the calendar. A pending booking without
        # doctor_id/start_at can no longer be verified (see
        # verify_pending_booking) — it would create an unschedulable row.
        wilson_at = _next_working_slot(hour=10, days_ahead=1)
        cole_at = _next_working_slot(hour=14, days_ahead=2)
        db.add_all(
            [
                models.PendingBooking(
                    org_id=org.id,
                    patient_name="James Wilson",
                    type_label=f"Follow-up · {dr_chen.name}",
                    when_label=_slot_label(wilson_at),
                    doctor_id=dr_chen.id,
                    start_at=wilson_at,
                    reason="Follow-up",
                ),
                models.PendingBooking(
                    org_id=org.id,
                    patient_name="Amanda Cole",
                    type_label=f"New Patient · {dr_foster.name}",
                    when_label=_slot_label(cole_at),
                    doctor_id=dr_foster.id,
                    start_at=cole_at,
                    reason="New patient consultation",
                ),
            ]
        )

        # --- EHR integrations ---
        db.add_all(
            [
                models.EHRIntegration(
                    org_id=org.id, name="Epic Systems", status="Connected — Real-time", connected=True,
                    detail="Bidirectional sync active for patient demographics, scheduling, clinical notes, and medication histories.",
                    meta1_label="Last Sync", meta1_value="2 mins ago",
                    meta2_label="Data Transferred (24h)", meta2_value="1.2 GB",
                ),
                models.EHRIntegration(
                    org_id=org.id, name="Oracle Cerner", status="Not Connected", connected=False,
                    detail="Enable integration to pull patient records and push AI-generated clinical summaries directly into Cerner Millennium.",
                    note="Requires IT Administrator credentials and API access enabled in your Cerner environment.",
                ),
                models.EHRIntegration(
                    org_id=org.id, name="athenahealth", status="Connected — Scheduled", connected=True,
                    detail="Syncing appointments, billing codes, and demographic updates every 15 minutes.",
                    meta1_label="Next sync in", meta1_value="4m 12s",
                ),
                models.EHRIntegration(
                    org_id=org.id, name="Veradigm (Allscripts)", status="Not Connected", connected=False,
                    detail="Connect to Veradigm EHR to synchronize clinical workflows and voice-to-text transcriptions.",
                ),
            ]
        )

        # --- knowledge base ---
        db.add_all(
            [
                models.KBDocument(org_id=org.id, name="Clinic_Hours_Policy_2024.pdf", size_bytes=184_000, status="Indexed"),
                models.KBDocument(org_id=org.id, name="New_Patient_Intake_Form.pdf", size_bytes=96_000, status="Indexing"),
            ]
        )
        db.add_all(
            [
                models.KBSource(org_id=org.id, url="healthlinkclinic.com/services", status="Indexed"),
            ]
        )
        db.add_all(
            [
                models.FAQ(org_id=org.id, question="What are your clinic hours?", answer="Monday–Friday 8:00 AM–6:00 PM, Saturday 9:00 AM–1:00 PM."),
                models.FAQ(org_id=org.id, question="Do you accept walk-ins?", answer="Walk-ins are accepted for urgent concerns; appointments are recommended for all other visits."),
            ]
        )

        # --- services ---
        # Committed with everything else below; the helper is reused so the
        # fresh path and the backfill path can never drift apart.
        db.flush()
        _backfill_demo_clinic_info(db, org)
        _backfill_demo_services(db, org)

        # --- routing rules ---
        db.add_all(
            [
                models.RoutingRule(org_id=org.id, title="Medical Emergencies", detail="Keywords: pain, bleeding, urgent, 911", enabled=True),
                models.RoutingRule(org_id=org.id, title="Complex Billing Issues", detail="Disputes, collections, payment plans", enabled=True),
            ]
        )

        # --- audit log ---
        #
        # HV-1 deliberately removed the five demo rows that used to be
        # planted here ("Patient Record Accessed / Voice Agent Alpha",
        # "Failed Login Attempt / Unknown (IP: 192.168.1.1)", ...). They
        # existed because nothing wrote to `audit_log`, so the Security
        # page needed something to render.
        #
        # Now that every PHI touch writes a real entry, seeding fake ones
        # is worse than an empty table: they are fabricated compliance
        # records on the page a clinic would show an auditor, and they are
        # indistinguishable from the real rows beside them. The demo org's
        # log fills itself the moment anyone signs in and opens a patient.

        # --- invoices ---
        db.add_all(
            [
                models.Invoice(org_id=org.id, invoice_number="INV-2023-1001", issued_at=datetime(2023, 10, 1), amount_cents=49900, status="Paid"),
                models.Invoice(org_id=org.id, invoice_number="INV-2023-0901", issued_at=datetime(2023, 9, 1), amount_cents=49900, status="Paid"),
                models.Invoice(org_id=org.id, invoice_number="INV-2023-0801", issued_at=datetime(2023, 8, 1), amount_cents=49900, status="Paid"),
            ]
        )

        db.commit()
        print("Seeded demo org 'HealthLink Clinic'.")
        print("Login: admin@healthlinkclinic.com / password123")
    finally:
        db.close()


if __name__ == "__main__":
    run()