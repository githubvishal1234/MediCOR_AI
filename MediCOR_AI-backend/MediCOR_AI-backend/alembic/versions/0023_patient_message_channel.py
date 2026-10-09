"""which channel a patient is messaged on

Revision ID: 0023_message_channel
Revises: 0022_speech_pace
Create Date: PF-04

  patients.message_channel   "whatsapp" | "sms" | "email" | NULL   String(16)

NULL means nobody has recorded a preference, and falls back to
`DEFAULT_MESSAGE_CHANNEL`. That is deliberately a different shape from
`contact_consent`, where an unanswered question has to mean "do not
send": here the question is only *how* to reach someone we already have
permission to reach, so a default is safe where it would not be there.

SMS needed no new integration. WhatsApp already went out through Twilio's
Messages API; SMS is the same endpoint, the same credentials and the same
payload, differing only in that neither number carries the "whatsapp:"
channel prefix. The whole of PF-04 is therefore this column, a sender
number in configuration, and a parameter.

It matters more than its size suggests: reminders only reduce no-shows
among patients who actually receive them, and the patients least likely
to have WhatsApp overlap heavily with those most likely to miss an
appointment.

Bounded at 16 for the usual reason on this deployment -- an unbounded
`String` is VARCHAR(MAX) on SQL Server and cannot be indexed, and "who do
we reach by SMS" is a plausible filter later.

No backfill: every existing patient keeps the deployment default, which
is the behaviour they had before this column existed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_message_channel"
down_revision: Union[str, None] = "0022_speech_pace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patients", sa.Column("message_channel", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("patients", "message_channel")
