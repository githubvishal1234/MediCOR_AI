r"""
Merge patient records that are the same person recorded twice.

    venv\Scripts\python.exe merge_duplicate_patients.py            # dry run
    venv\Scripts\python.exe merge_duplicate_patients.py --apply

WHY THESE EXIST

`GET /patients?search=` used to match the phone column as a string, so
Twilio's E.164 caller id ("+918688460308") never matched a number a
receptionist had typed ("8688460308"). On every call from a returning
patient the lookup came back empty, the agent concluded the caller was
new, and `register_patient` created a second record for them.

That search is fixed (see `_phone_suffix` in `routers/patients.py`), so
this is cleaning up after a bug rather than papering over a live one. It
stays in the tree because a duplicate can still arrive by other routes --
two receptionists typing the same person, an import, a smoke-test run --
and because doing this by hand against a live database is exactly the
sort of job that should be written down once.

WHY MERGE AND NOT ARCHIVE

Both are tempting to just hide. The reason that is wrong here is visible
in the data: each duplicated Krishna row carries one call, and each
duplicated smoke-test row carries one appointment. **The history is split
across the pair.** Archiving one would leave its call or appointment
attached to a record nobody looks at, which is how a patient turns up
saying they have an appointment and the clinic cannot find it.

So every table that references the loser is re-pointed at the keeper
first -- all nine of them, enumerated from the metadata rather than
remembered -- and only then is the loser archived.

WHICH ROW WINS

The oldest. It is the original; the newer one is the artifact. Where the
two disagree on a field (an email on one and not the other), the keeper
takes any value it is missing, so merging cannot lose information.

WHY ARCHIVE RATHER THAN DELETE

`status="Archived"` is what this schema already means by removing a
patient (see `routers/patients.py`) -- the row leaves the roster and
stays reachable by id. A hard DELETE would break any external reference
to that MRN and cannot be undone. Losers keep their id, so the merge is
reversible from the backup this writes.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import audit_service, models  # noqa: E402
from app.database import Base, SessionLocal  # noqa: E402

# The last N digits that identify a line, matching `_phone_suffix` in
# routers/patients.py. Ten is the national subscriber length in both
# India and the US.
PHONE_DIGITS = 10


def phone_key(value):
    """The comparable form of a phone number, or None if there isn't one."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return digits[-PHONE_DIGITS:] if len(digits) >= 7 else None


def patient_references():
    """
    Every (table, column) pointing at `patients.id`, read from the
    metadata rather than listed by hand.

    Nine today. A hand-written list is a list that goes stale the next
    time a feature adds a patient-scoped table, and the failure mode is
    silent: the merge appears to work and one table still points at an
    archived row.
    """
    found = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            for fk in column.foreign_keys:
                if fk.column.table.name == "patients":
                    found.append((table.name, column.name))
    return found


def find_duplicates(db):
    """
    Groups of patients in one org sharing a phone line.

    **Archived rows are excluded, and that is not a detail.** This script
    archives the losing side of every merge it performs, so without the
    filter a second run finds the same pair again -- keeper plus the row
    it already absorbed -- and offers to merge a record that has nothing
    left in it into the one that took everything. Harmless but alarming,
    and it makes the dry run useless as a "is there anything to do?"
    check, which is most of what it is for.

    It also matches `find_existing_patient` in routers/patients.py, which
    excludes archived rows for the same reason.
    """
    groups = defaultdict(list)
    rows = (
        db.query(models.Patient)
        .filter(models.Patient.status != "Archived")
        .all()
    )
    for patient in rows:
        key = phone_key(patient.phone)
        if key:
            groups[(patient.org_id, key)].append(patient)
    return {k: v for k, v in groups.items() if len(v) > 1}


def describe(db, patient, references):
    counts = {}
    for table_name, column in references:
        table = Base.metadata.tables[table_name]
        # Counted by fetching rather than by `rowcount`, which is not
        # meaningful for a SELECT on every driver.
        rows = db.execute(
            table.select().where(table.c[column] == patient.id)
        ).fetchall()
        if rows:
            counts[table_name] = len(rows)
    return counts


# Fields copied onto the keeper when it has none and the loser does.
# Deliberately a short list of facts about the person, not every column:
# status and timestamps belong to the row, not to the patient.
_FILLABLE = (
    "phone", "email", "dob", "gender", "address",
    "preferred_language", "speech_pace_preference", "message_channel",
    "contact_consent", "contact_consent_at", "contact_consent_source",
)


def merge(db, keeper, loser, references, apply):
    """Re-point everything at `keeper`, fill its gaps, archive `loser`."""
    moved = {}
    for table_name, column in references:
        table = Base.metadata.tables[table_name]
        rows = db.execute(
            table.select().where(table.c[column] == loser.id)
        ).fetchall()
        if not rows:
            continue
        moved[table_name] = len(rows)
        if apply:
            db.execute(
                table.update()
                .where(table.c[column] == loser.id)
                .values(**{column: keeper.id})
            )

    filled = {}
    for field in _FILLABLE:
        if not hasattr(keeper, field):
            continue
        if not getattr(keeper, field, None) and getattr(loser, field, None):
            filled[field] = getattr(loser, field)
            if apply:
                setattr(keeper, field, getattr(loser, field))

    if apply:
        loser.status = "Archived"
        audit_service.record(
            db,
            keeper.org_id,
            "patient.archived",
            who="merge_duplicate_patients.py",
            actor_type="system",
            target_type="patient",
            target_id=loser.id,
            detail=(
                f"Merged duplicate into {keeper.id} ({keeper.mrn}). "
                f"Moved: {json.dumps(moved) if moved else 'nothing'}. "
                f"Filled on keeper: {sorted(filled) if filled else 'nothing'}."
            ),
        )
    return moved, filled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="actually merge. Without it, nothing is written.",
    )
    parser.add_argument(
        "--backup-dir", default="_backups",
        help="where the pre-merge snapshot is written",
    )
    args = parser.parse_args()

    references = patient_references()
    db = SessionLocal()
    try:
        duplicates = find_duplicates(db)
        if not duplicates:
            print("No duplicate patients found.")
            return 0

        print(f"{len(references)} tables reference patients.id")
        print(f"{len(duplicates)} duplicate group(s)\n")

        snapshot = []
        plan = []
        for (org_id, key), patients in sorted(duplicates.items()):
            ordered = sorted(patients, key=lambda p: p.created_at or datetime.min)
            keeper, losers = ordered[0], ordered[1:]
            print(f"=== {key}  (org {org_id[:8]}) ===")
            print(f"  KEEP    {keeper.id}  {keeper.name!r}  {keeper.mrn}  "
                  f"created {keeper.created_at}  {describe(db, keeper, references)}")
            for loser in losers:
                print(f"  MERGE   {loser.id}  {loser.name!r}  {loser.mrn}  "
                      f"created {loser.created_at}  {describe(db, loser, references)}")
                plan.append((keeper, loser))
            for p in ordered:
                snapshot.append({
                    c.name: (v.isoformat() if isinstance(v := getattr(p, c.name), datetime) else v)
                    for c in models.Patient.__table__.columns
                })
            print()

        if not args.apply:
            print("DRY RUN - nothing written. Re-run with --apply.")
            return 0

        os.makedirs(args.backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(args.backup_dir, f"patients-pre-merge-{stamp}.json")
        with open(backup, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, default=str)
        print(f"Backup written: {backup}\n")

        for keeper, loser in plan:
            moved, filled = merge(db, keeper, loser, references, apply=True)
            print(f"  merged {loser.id} -> {keeper.id}")
            print(f"     moved:  {moved or 'nothing'}")
            print(f"     filled: {sorted(filled) or 'nothing'}")
        db.commit()
        print("\nDone.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
