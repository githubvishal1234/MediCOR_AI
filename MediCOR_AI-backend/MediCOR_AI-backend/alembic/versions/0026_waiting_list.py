"""record unmet demand so cancellations can be filled

Revision ID: 0026_waiting_list
Revises: 0025_patient_feedback
Create Date: PF-15

A cancellation is pure lost capacity. The slot reopens, and whoever wanted
it has already been told there was nothing and gone away. Availability
computation has always been exact and a cancellation has always released
its slot immediately -- what was missing was any record of who wanted one.

**One entry per patient per clinician**, enforced in the router rather
than by a constraint: a caller who rings three times about the same thing
should move up the queue by seniority, not hold three places in it. Not a
database UNIQUE because the condition is on *live* statuses only, and a
filtered unique index is a dialect-specific thing this schema has avoided
elsewhere.

**The offer is exclusive but the slot is not held.** `offered_start_at` /
`offer_expires_at` make an offer belong to one patient for a bounded
time. Deliberately no placeholder appointment: a "held" status would
ripple into the calendar, the availability grid, conflict detection,
analytics and the stale-call reaper, and each of those is a place for a
phantom booking to leak out. The cost is a real race -- a walk-in can take
an offered slot first -- which is handled by running the ordinary
`_validate_slot` on acceptance and putting the entry back on the list.
Losing a slot you were offered is disappointing; an appointment nobody can
see is worse.

`wanted_from` / `wanted_until` are clinic **wall-clock** naive, like
`appointments.start_at` and for the same reason -- see app/timezones.py.

Indexed on (org_id, status): the query this table exists to serve is
"who in this clinic is still waiting", run every sweep.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_waiting_list"
down_revision: Union[str, None] = "0025_patient_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "waiting_list_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("patient_id", sa.String(length=64), nullable=False),
        sa.Column("doctor_id", sa.String(length=64), nullable=True),
        sa.Column("wanted_from", sa.DateTime(), nullable=True),
        sa.Column("wanted_until", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("offered_start_at", sa.DateTime(), nullable=True),
        sa.Column("offered_at", sa.DateTime(), nullable=True),
        sa.Column("offer_expires_at", sa.DateTime(), nullable=True),
        sa.Column("offers_made", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resulting_appointment_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"]),
        sa.ForeignKeyConstraint(["resulting_appointment_id"], ["appointments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_waiting_list_org_status", "waiting_list_entries", ["org_id", "status"]
    )
    op.create_index(
        "ix_waiting_list_entries_patient_id", "waiting_list_entries", ["patient_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_waiting_list_entries_patient_id", table_name="waiting_list_entries")
    op.drop_index("ix_waiting_list_org_status", table_name="waiting_list_entries")
    op.drop_table("waiting_list_entries")
