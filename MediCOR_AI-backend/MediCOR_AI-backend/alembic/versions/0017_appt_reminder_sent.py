"""appointments.reminder_sent_at: don't remind the same patient twice

Revision ID: 0017_appt_reminder_sent
Revises: 0016_refill_requests
Create Date: F3

`notification_service` could already compose and send an appointment
message, but only in response to a request — so a reminder, which by
definition has to fire at a time rather than on a click, was structurally
impossible until the scheduler landed (F2, migration 0013).

One nullable UTC timestamp. NULL means "not yet reminded", which is the
correct state for every existing row: back-filling a value would suppress
the first reminder for every appointment already in the calendar, and
back-filling NULL is what the column already does.

It is the idempotency guard. The scheduler's `run_key` prevents two
workers racing the same tick; this prevents the same patient being
reminded again on the *next* tick, after a restart, or if
`APPOINTMENT_REMINDER_LEAD_HOURS` is widened.

Deliberately NOT added here: a `confirmation_status` column. Two-way
confirmation needs an inbound WhatsApp webhook this deployment does not
have, and shipping a column nothing writes is the failure this codebase
already carries twice over — `call_logs.recording_url` (a working play
button over a column no code populates) and the pre-HV-4
`voice_minutes_used`. The column arrives with the feature that fills it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_appt_reminder_sent"
down_revision: Union[str, None] = "0016_refill_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("reminder_sent_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("appointments", "reminder_sent_at")
