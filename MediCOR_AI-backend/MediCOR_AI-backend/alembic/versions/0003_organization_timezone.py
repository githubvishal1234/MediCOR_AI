"""organizations: add timezone

Revision ID: 0003_organization_timezone
Revises: 0002_pending_booking_fields
Create Date: Phase 1

Scheduling used to compare clinic-local working hours against
`datetime.utcnow()`, so for any clinic not on UTC the offered slot grid
was shifted by the UTC offset and "cannot book an appointment in the
past" fired across most of the working day.

This column records each clinic's IANA zone. It defaults to "UTC", which
reproduces the previous behaviour exactly — an existing install sees no
change until it sets a real zone from the Clinic Profile page.

Existing `appointments.start_at` values already hold clinic-local
wall-clock time, so no backfill is required. See docs/TIMEZONES.md.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_organization_timezone"
down_revision: Union[str, None] = "0002_pending_booking_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "organizations"
_COLUMN = "timezone"


def _columns() -> set:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    existing = _columns()
    if not existing or _COLUMN in existing:
        # Table absent (baseline creates it with the column), or already
        # added by the legacy sync_missing_columns() bootstrap.
        return

    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(64), nullable=True))
    # Backfill explicitly: the model default is client-side, so rows that
    # already exist would otherwise keep a NULL and rely on every read
    # path coalescing it.
    op.execute(
        sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = 'UTC' WHERE {_COLUMN} IS NULL")
    )


def downgrade() -> None:
    if _COLUMN in _columns():
        op.drop_column(_TABLE, _COLUMN)
