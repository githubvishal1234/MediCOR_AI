r"""
Remove the call_log rows left behind by agent dispatch tests.

`dispatchtest.py` creates a real room and dispatches the agent into it to
prove the worker is registered. The agent, correctly, treats that as a
call and writes a call_log row — which then counts toward "calls handled
today" on the dashboard and drags the resolution rate down.

Only rows matching ALL of these are touched, so a genuine short call is
never caught: no transcript messages, outcome "No Interaction", and a
duration under 60 seconds.

Run from the backend root with the venv active:

    python cleanup_dispatch_test_calls.py            # dry run, lists them
    python cleanup_dispatch_test_calls.py --delete   # actually deletes
"""
import sys

from app.database import SessionLocal
from app import models

DRY_RUN = "--delete" not in sys.argv


def main() -> None:
    db = SessionLocal()
    try:
        candidates = []
        for row in db.query(models.CallLog).all():
            if row.outcome != "No Interaction":
                continue
            if (row.duration_seconds or 0) >= 60:
                continue
            msgs = (
                db.query(models.TranscriptMessage)
                .filter(models.TranscriptMessage.call_id == row.id)
                .count()
            )
            if msgs:
                continue
            candidates.append(row)

        if not candidates:
            print("Nothing to clean up.")
            return

        print(f"{len(candidates)} dispatch-test row(s):")
        for row in candidates:
            print(
                f"  {row.id[:12]}  started={row.started_at}  "
                f"{row.duration_seconds}s  {row.outcome!r}"
            )

        if DRY_RUN:
            print("\nDry run. Re-run with --delete to remove them.")
            return

        ids = [row.id for row in candidates]
        evaluations = (
            db.query(models.CallEvaluation)
            .filter(models.CallEvaluation.call_id.in_(ids))
            .count()
        )
        if evaluations:
            print(f"Removing {evaluations} dependent call_evaluations row(s) first.")
            db.query(models.CallEvaluation).filter(
                models.CallEvaluation.call_id.in_(ids)
            ).delete(synchronize_session=False)

        for row in candidates:
            db.delete(row)
        db.commit()
        print(f"\nDeleted {len(candidates)} row(s).")
        print(f"Remaining call_logs: {db.query(models.CallLog).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
