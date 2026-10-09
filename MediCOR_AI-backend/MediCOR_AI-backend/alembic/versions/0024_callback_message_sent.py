"""record when an automatic apology went out for a missed call

Revision ID: 0024_callback_message
Revises: 0023_message_channel
Create Date: PF-12

  call_logs.callback_message_sent_at   DateTime, nullable

**Deliberately not `callback_status = "contacted"`.** That column is the
*human* queue -- pending, contacted, resolved -- and a machine sending
"sorry we missed you" is not a person having called the patient back.
Writing it there would quietly empty the queue staff are meant to work,
which is the opposite of what this feature is for. Two facts, two
columns.

The marker is written when a message is **dispatched**, not when delivery
is confirmed. The transports swallow their own failures by design, so
arrival is not knowable, and retrying forever against an address that
will never accept mail is the worse failure -- the same rule
`appointments.reminder_sent_at` follows. A patient with no contact
details, or who has not consented, is left unmarked: nothing was claimed
on their behalf, so there is nothing to suppress next time.

No backfill. NULL means no automatic message has gone out for that call,
which is true of every row when this runs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_callback_message"
down_revision: Union[str, None] = "0023_message_channel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "call_logs", sa.Column("callback_message_sent_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("call_logs", "callback_message_sent_at")
