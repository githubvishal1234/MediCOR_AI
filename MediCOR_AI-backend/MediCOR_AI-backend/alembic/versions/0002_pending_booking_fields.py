"""pending_bookings: add structured booking intent (patient_id, doctor_id, start_at, reason)

Revision ID: 0002_pending_booking_fields
Revises: 0001_baseline
Create Date: Phase 0

Verifying a pending booking used to create an Appointment with only a
title, a patient name and a time *label* — no start_at, no doctor. Such a
row never renders on the calendar (which places appointments by start_at)
and is invisible to conflict checking (which filters start_at IS NOT
NULL), so the slot stayed bookable by someone else.

These four nullable columns let a pending booking carry the actual
booking intent, so `verify_pending_booking` can build a real, validated
appointment. Nullable because rows predating this revision have no such
data; verify refuses those explicitly rather than creating a phantom.

Idempotent by design — see the note in 0001_baseline about the adoption
baseline creating tables from current model metadata.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_pending_booking_fields"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "pending_bookings"

_NEW_COLUMNS = (
    ("patient_id", sa.Column("patient_id", sa.String(64), nullable=True)),
    ("doctor_id", sa.Column("doctor_id", sa.String(64), nullable=True)),
    ("start_at", sa.Column("start_at", sa.DateTime(), nullable=True)),
    ("reason", sa.Column("reason", sa.String(), nullable=True)),
)


def _existing_columns() -> set:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    existing = _existing_columns()
    if not existing:
        # Table itself is missing — the baseline never ran. Nothing to do;
        # create_all() in 0001 builds it with these columns already.
        return
    for name, column in _NEW_COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, column)


def downgrade() -> None:
    existing = _existing_columns()
    for name, _ in reversed(_NEW_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
