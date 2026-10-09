"""
Phase 1 — clinic-local scheduling, doctor management, patient archiving.

The headline fix: appointment times are clinic wall-clock readings, but
`_validate_slot` and `get_availability` compared them against
`datetime.utcnow()`. For a clinic at UTC+5:30 that shifted the whole
offered slot grid and made "cannot book an appointment in the past" fire
across most of the working day.

Runs against throwaway in-memory SQLite; never touches the configured
SQL Server.

    pytest tests/test_phase1_scheduling.py -v
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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, rate_limit, timezones
from app.database import Base, get_db
from app.routers import appointments as appointments_router
from app.routers import doctors as doctors_router
from app.routers import knowledge_base as kb_router
from app.routers import patients as patients_router
from app.security import create_access_token


@pytest.fixture()
def ctx():
    rate_limit._attempts.clear()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    for router in (
        appointments_router.router,
        doctors_router.router,
        patients_router.router,
        kb_router.router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c, TestingSessionLocal


def _make_clinic(session_factory, tz_name="UTC", role=models.UserRole.admin, with_doctor=True):
    db = session_factory()
    try:
        org = models.Organization(name=f"Clinic {tz_name}", timezone=tz_name)
        db.add(org)
        db.flush()
        user = models.User(
            org_id=org.id,
            email=f"user-{org.id}@clinic.example",
            hashed_password="unused",
            full_name="Test User",
            role=role,
        )
        db.add(user)
        doctor_id = None
        if with_doctor:
            doctor = models.Doctor(
                org_id=org.id,
                name="Dr. Nine To Five",
                slot_minutes=30,
                work_start_hour=9,
                work_end_hour=17,
                work_days_csv="0,1,2,3,4,5,6",  # every day, so the test never lands on a closed weekend
                active=True,
            )
            db.add(doctor)
            db.flush()
            doctor_id = doctor.id
        db.commit()
        db.refresh(user)
        headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
        return headers, org.id, doctor_id
    finally:
        db.close()


def _tz_for_local_hour(desired_hour: int) -> str:
    """
    An Etc/GMT zone in which the clinic's current local hour is
    `desired_hour`, whatever the machine's UTC time happens to be.

    Etc/GMT offsets are sign-inverted: Etc/GMT-14 is UTC+14.
    """
    offset = (desired_hour - datetime.utcnow().hour) % 24
    if offset > 14:
        offset -= 24  # keep inside the Etc/GMT-14 .. Etc/GMT+12 range
    if offset == 0:
        return "UTC"
    return f"Etc/GMT-{offset}" if offset > 0 else f"Etc/GMT+{abs(offset)}"


# ---------------------------------------------------------------------
# The timezone helpers (pure functions — fully deterministic)
# ---------------------------------------------------------------------

def test_now_local_tracks_the_clinic_not_utc():
    kolkata = timezones.resolve("Asia/Kolkata")
    delta = timezones.now_local(kolkata) - datetime.utcnow()
    # +05:30, allowing a second of drift between the two clock reads.
    assert abs(delta - timedelta(hours=5, minutes=30)) < timedelta(seconds=5)


def test_unknown_timezone_falls_back_to_utc_without_raising():
    tz = timezones.resolve("Mars/Olympus_Mons")
    assert abs(timezones.now_local(tz) - datetime.utcnow()) < timedelta(seconds=5)
    assert timezones.is_valid("Mars/Olympus_Mons") is False
    assert timezones.is_valid("Asia/Kolkata") is True


def test_tzdata_is_installed():
    """
    Windows ships no system timezone database. Without the `tzdata`
    package every clinic silently falls back to UTC — which is exactly
    the bug this phase fixes, reintroduced as a packaging problem.
    """
    assert timezones.is_valid("Asia/Kolkata"), (
        "Asia/Kolkata could not be resolved — the `tzdata` package is missing "
        "from this environment (it is pinned in requirements.txt)."
    )
    assert len(timezones.all_timezone_names()) > 100


def test_local_day_bounds_are_a_utc_range():
    kolkata = timezones.resolve("Asia/Kolkata")
    start, end = timezones.local_day_bounds_utc(kolkata)
    assert end - start == timedelta(days=1)
    # Clinic midnight at UTC+05:30 is 18:30 UTC the previous day.
    assert (start.hour, start.minute) == (18, 30)


def test_utc_offset_label():
    assert timezones.utc_offset_label(timezones.resolve("Asia/Kolkata")) == "UTC+05:30"
    assert timezones.utc_offset_label(timezones.resolve("UTC")) == "UTC+00:00"


# ---------------------------------------------------------------------
# Availability honours the clinic's clock
# ---------------------------------------------------------------------
# These two cases are complementary on purpose. Availability used to
# filter against datetime.utcnow(); whichever hour the machine running
# these tests happens to be at, at least one of the two below places
# UTC on the wrong side of the doctor's 9-5 window and so fails against
# the old implementation.

def test_no_slots_offered_after_the_clinic_day_has_ended(ctx):
    client, session_factory = ctx
    headers, _, doctor_id = _make_clinic(session_factory, tz_name=_tz_for_local_hour(20))

    tz = timezones.resolve(_tz_for_local_hour(20))
    today_local = timezones.now_local(tz).date()

    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": today_local.isoformat()},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["slots"] == [], (
        "It is 8 PM at the clinic and the doctor works 9-5, but slots were "
        "still offered for today — availability is not using the clinic's clock."
    )


def test_whole_day_offered_before_the_clinic_opens(ctx):
    client, session_factory = ctx
    tz_name = _tz_for_local_hour(6)
    headers, _, doctor_id = _make_clinic(session_factory, tz_name=tz_name)

    tz = timezones.resolve(tz_name)
    today_local = timezones.now_local(tz).date()

    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": today_local.isoformat()},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert len(slots) == 16, (
        f"It is 6 AM at the clinic and the doctor works 9-5 on 30-minute slots, "
        f"so the whole day should be open — got {len(slots)} slots."
    )
    assert slots[0]["label"] == "9:00 AM"
    assert slots[-1]["label"] == "4:30 PM"


def _expected_slot_count(reference_now, day, start_h=9, end_h=17, step_min=30):
    """How many 30-minute slots in a 9-5 day fall strictly after `reference_now`."""
    cursor = datetime.combine(day, datetime.min.time()).replace(hour=start_h)
    end = datetime.combine(day, datetime.min.time()).replace(hour=end_h)
    count = 0
    while cursor + timedelta(minutes=step_min) <= end:
        if cursor > reference_now:
            count += 1
        cursor += timedelta(minutes=step_min)
    return count


def test_availability_filters_on_clinic_now_not_utc_now(ctx):
    """
    The discriminating test for the timezone fix.

    Places the clinic at midday local — inside the doctor's 9-5 window —
    so the set of remaining slots is decided by *which clock* the filter
    reads. Computes both answers and asserts the endpoint gives the
    clinic-local one.

    This separates the two implementations at every UTC hour except the
    one where the chosen zone lands on offset zero, where there is by
    definition nothing to tell apart.
    """
    client, session_factory = ctx
    tz_name = _tz_for_local_hour(12)
    headers, _, doctor_id = _make_clinic(session_factory, tz_name=tz_name)

    tz = timezones.resolve(tz_name)
    local_now = timezones.now_local(tz)
    day = local_now.date()

    expected_local = _expected_slot_count(local_now, day)
    expected_if_utc = _expected_slot_count(datetime.utcnow(), day)

    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": day.isoformat()},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    actual = len(r.json()["slots"])

    assert actual == expected_local, (
        f"clinic is at {tz_name} and its local time is {local_now:%H:%M}; expected "
        f"{expected_local} remaining slots but got {actual}. A UTC-based filter "
        f"would have given {expected_if_utc}."
    )
    if expected_local != expected_if_utc:
        assert actual != expected_if_utc, "availability is still filtering on UTC"


def test_booking_a_slot_the_clinic_considers_future_succeeds(ctx):
    """
    A morning slot on a clinic whose local time is still before dawn must
    be bookable. Against the old code this raised "Cannot book an
    appointment in the past" whenever UTC had already passed that hour.
    """
    client, session_factory = ctx
    tz_name = _tz_for_local_hour(6)
    headers, org_id, doctor_id = _make_clinic(session_factory, tz_name=tz_name)

    db = session_factory()
    try:
        patient = models.Patient(org_id=org_id, mrn="MRN-1", name="Early Bird")
        db.add(patient)
        db.commit()
        patient_id = patient.id
    finally:
        db.close()

    tz = timezones.resolve(tz_name)
    today_local = timezones.now_local(tz).date()
    start_at = datetime.combine(today_local, datetime.min.time()).replace(hour=9)

    r = client.post(
        "/appointments/book",
        json={
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "start_at": start_at.isoformat(),
        },
        headers=headers,
    )
    assert r.status_code == 200, f"clinic-local 9 AM was rejected: {r.text}"
    assert r.json()["time_label"] == "9:00 AM"


def test_availability_slots_are_naive_clinic_local_strings(ctx):
    """
    The voice agent's `_resolve_start_at` fallback matches a bare "10:00"
    against `slot["start_at"][-8:-3]`, which only works while these stay
    naive `YYYY-MM-DDTHH:MM:SS` clinic-local strings. Changing the shape
    here silently breaks slot recovery on live calls.
    """
    client, session_factory = ctx
    headers, _, doctor_id = _make_clinic(session_factory, tz_name="Asia/Kolkata")

    tz = timezones.resolve("Asia/Kolkata")
    target = (timezones.now_local(tz) + timedelta(days=3)).date()
    r = client.get(
        "/appointments/availability",
        params={"doctor_id": doctor_id, "date": target.isoformat()},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    for slot in r.json()["slots"]:
        raw = slot["start_at"]
        assert "+" not in raw and not raw.endswith("Z"), f"slot carries an offset: {raw}"
        assert len(raw) == 19, f"unexpected slot format: {raw}"
        assert raw[-8:-3] == datetime.fromisoformat(raw).strftime("%H:%M")


# ---------------------------------------------------------------------
# Doctor management
# ---------------------------------------------------------------------

def test_doctor_crud_lifecycle(ctx):
    client, session_factory = ctx
    headers, _, _ = _make_clinic(session_factory, with_doctor=False)

    r = client.post(
        "/doctors",
        json={
            "name": "  Dr. Amara Osei  ",
            "specialty": "Paediatrics",
            "slot_minutes": 20,
            "work_start_hour": 8,
            "work_end_hour": 14,
            "work_days": [2, 0, 1, 1],
            "consultation_fee_label": "$90",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    doctor = r.json()
    assert doctor["name"] == "Dr. Amara Osei"
    assert doctor["work_days"] == [0, 1, 2], "work_days should be de-duplicated and sorted"
    doctor_id = doctor["id"]

    # The lean agent-facing list picks it up immediately.
    r = client.get("/doctors", headers=headers)
    assert r.status_code == 200 and [d["id"] for d in r.json()] == [doctor_id]
    assert "work_days" not in r.json()[0], "GET /doctors must stay lean for the voice agent"

    r = client.patch(f"/doctors/{doctor_id}", json={"work_end_hour": 18}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["work_end_hour"] == 18

    # Soft delete: gone from booking, still present in the full list.
    assert client.delete(f"/doctors/{doctor_id}", headers=headers).status_code == 204
    assert client.get("/doctors", headers=headers).json() == []
    all_doctors = client.get("/doctors/all", headers=headers).json()
    assert len(all_doctors) == 1 and all_doctors[0]["active"] is False

    # Reactivating restores them.
    r = client.patch(f"/doctors/{doctor_id}", json={"active": True}, headers=headers)
    assert r.status_code == 200 and r.json()["active"] is True
    assert len(client.get("/doctors", headers=headers).json()) == 1


def test_patching_hours_cannot_invert_the_working_day(ctx):
    client, session_factory = ctx
    headers, _, doctor_id = _make_clinic(session_factory)

    # 9-17 doctor; moving only the start past the end must be refused
    # rather than producing a doctor who works a negative day.
    r = client.patch(f"/doctors/{doctor_id}", json={"work_start_hour": 20}, headers=headers)
    assert r.status_code == 422, r.text


def test_doctor_writes_are_admin_only(ctx):
    client, session_factory = ctx
    staff_headers, _, doctor_id = _make_clinic(
        session_factory, role=models.UserRole.medical_staff
    )

    assert client.post("/doctors", json={"name": "Dr. No"}, headers=staff_headers).status_code == 403
    assert client.patch(f"/doctors/{doctor_id}", json={"slot_minutes": 15}, headers=staff_headers).status_code == 403
    assert client.delete(f"/doctors/{doctor_id}", headers=staff_headers).status_code == 403
    # ...but staff can still read, because they need it to book.
    assert client.get("/doctors/all", headers=staff_headers).status_code == 200


def test_doctors_are_org_scoped(ctx):
    client, session_factory = ctx
    headers_a, _, doctor_a = _make_clinic(session_factory)
    headers_b, _, _ = _make_clinic(session_factory)

    assert client.patch(f"/doctors/{doctor_a}", json={"slot_minutes": 15}, headers=headers_b).status_code == 404
    assert client.delete(f"/doctors/{doctor_a}", headers=headers_b).status_code == 404
    assert doctor_a not in [d["id"] for d in client.get("/doctors/all", headers=headers_b).json()]


# ---------------------------------------------------------------------
# Patient archiving
# ---------------------------------------------------------------------

def test_archiving_a_patient_preserves_the_medical_record(ctx):
    """
    DELETE used to hard-delete, and prescriptions/interactions/appointments
    all cascade with delete-orphan — so removing a patient destroyed their
    entire clinical history.
    """
    client, session_factory = ctx
    headers, _, _ = _make_clinic(session_factory, with_doctor=False)

    patient_id = client.post("/patients", json={"name": "Archive Me"}, headers=headers).json()["id"]
    rx_id = client.post(
        f"/patients/{patient_id}/prescriptions",
        json={"name": "Metformin", "detail": "500mg"},
        headers=headers,
    ).json()["id"]

    assert client.delete(f"/patients/{patient_id}", headers=headers).status_code == 204

    # Record intact, marked archived, and out of the default roster.
    r = client.get(f"/patients/{patient_id}", headers=headers)
    assert r.status_code == 200, "archiving destroyed the patient record"
    assert r.json()["status"] == "Archived"
    assert rx_id in [p["id"] for p in r.json()["prescriptions"]], "prescriptions were cascaded away"

    assert client.get("/patients", headers=headers).json() == []
    assert patient_id in [p["id"] for p in client.get("/patients?include_archived=true", headers=headers).json()]

    # And it is reversible.
    assert client.post(f"/patients/{patient_id}/restore", headers=headers).status_code == 200
    assert [p["id"] for p in client.get("/patients", headers=headers).json()] == [patient_id]


def test_purge_is_admin_only_and_still_available(ctx):
    client, session_factory = ctx
    staff_headers, org_id, _ = _make_clinic(
        session_factory, role=models.UserRole.medical_staff, with_doctor=False
    )
    patient_id = client.post("/patients", json={"name": "Erase Me"}, headers=staff_headers).json()["id"]

    r = client.delete(f"/patients/{patient_id}?purge=true", headers=staff_headers)
    assert r.status_code == 403, "non-admin staff must not be able to destroy a medical record"
    assert client.get(f"/patients/{patient_id}", headers=staff_headers).status_code == 200

    db = session_factory()
    try:
        admin = models.User(
            org_id=org_id,
            email=f"admin-{org_id}@clinic.example",
            hashed_password="unused",
            full_name="Admin",
            role=models.UserRole.admin,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': admin.id})}"}
    finally:
        db.close()

    assert client.delete(f"/patients/{patient_id}?purge=true", headers=admin_headers).status_code == 204
    assert client.get(f"/patients/{patient_id}", headers=admin_headers).status_code == 404


# ---------------------------------------------------------------------
# Clinic profile: timezone + services
# ---------------------------------------------------------------------

def test_clinic_profile_round_trips_the_timezone(ctx):
    client, session_factory = ctx
    headers, _, _ = _make_clinic(session_factory, with_doctor=False)

    r = client.get("/knowledge-base/clinic-info", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["timezone"] == "UTC"

    r = client.put(
        "/knowledge-base/clinic-info",
        json={"timezone": "Asia/Kolkata", "timings_text": "Mon-Fri 9-5", "phone": "+91 80 1234 5678"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timezone"] == "Asia/Kolkata"
    assert body["timezone_label"] == "UTC+05:30"
    assert body["timings_text"] == "Mon-Fri 9-5"

    # And it actually took effect on the org, not just in the response.
    assert client.get("/knowledge-base/clinic-info", headers=headers).json()["timezone"] == "Asia/Kolkata"


def test_invalid_timezone_is_rejected_not_silently_ignored(ctx):
    client, session_factory = ctx
    headers, _, _ = _make_clinic(session_factory, with_doctor=False)

    r = client.put("/knowledge-base/clinic-info", json={"timezone": "Middle/Earth"}, headers=headers)
    assert r.status_code == 422, (
        "an unusable timezone must fail loudly — silently falling back to UTC "
        "would shift every future booking without telling anyone"
    )
    assert client.get("/knowledge-base/clinic-info", headers=headers).json()["timezone"] == "UTC"


def test_timezone_picker_list_is_populated(ctx):
    client, session_factory = ctx
    headers, _, _ = _make_clinic(session_factory, with_doctor=False)
    r = client.get("/knowledge-base/timezones", headers=headers)
    assert r.status_code == 200
    assert "Asia/Kolkata" in r.json()


def test_services_can_be_edited_in_place(ctx):
    client, session_factory = ctx
    headers, _, _ = _make_clinic(session_factory, with_doctor=False)

    service_id = client.post(
        "/knowledge-base/services",
        json={"name": "Consultation", "fee_label": "$50"},
        headers=headers,
    ).json()["id"]

    r = client.patch(
        f"/knowledge-base/services/{service_id}",
        json={"fee_label": "$65"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["fee_label"] == "$65"
    assert r.json()["name"] == "Consultation", "a partial update must not blank other fields"
