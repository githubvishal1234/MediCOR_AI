"""post-visit follow-up, in the clinic's own words

Revision ID: 0028_follow_up
Revises: 0027_pre_visit
Create Date: PF-14

  clinic_info.follow_up_message      NVARCHAR(MAX), nullable
  clinic_info.follow_up_delay_days   Integer, default 3
  appointments.follow_up_sent_at     DateTime, nullable

Nothing happened after a visit. Aftercare depended on what the patient
remembered, and a follow-up booking on them thinking of it.

**THE CLINIC WRITES THE WORDS, AND NOTHING SHIPS WITH A DEFAULT.**

That is the design, not a gap in it. Aftercare wording is a clinical
statement -- what to expect, when to worry, when to come back -- and a
software vendor composing it on a clinic's behalf, to go out over that
clinic's name, is not a defensible position. `follow_up_message` is
therefore NULL on every existing row and the scheduled job skips any
clinic that has not written one. No content, no feature.

This is the same call `EMERGENCY_NUMBER` / `EMERGENCY_INSTRUCTION` made
for the voice prompt, and for the same reason.

`{name}` is the only substitution the sender performs. A template
language here would be a way for a settings screen to raise exceptions
inside a scheduled job.

`follow_up_delay_days` is counted from the appointment's own wall-clock
start, not from when the job runs -- `appointments.start_at` is clinic
wall-clock naive (see app/timezones.py), so the window is computed against
each clinic's own clock.

`follow_up_sent_at` is written on **dispatch**, not on confirmed delivery,
for the same reason as `reminder_sent_at`: the transports swallow their
own failures by design, so arrival is not knowable, and sending twice is
worse than not knowing.

`follow_up_message` is NVARCHAR(MAX) rather than `Text`. A clinic writing
aftercare in Hindi or Telugu is exactly the case that cost this schema a
set of transcripts once already -- see 0012 and its repair in 0021.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0028_follow_up"
down_revision: Union[str, None] = "0027_pre_visit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEXT = sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")


def upgrade() -> None:
    op.add_column("clinic_info", sa.Column("follow_up_message", _TEXT, nullable=True))
    op.add_column(
        "clinic_info", sa.Column("follow_up_delay_days", sa.Integer(), nullable=True)
    )
    op.add_column(
        "appointments", sa.Column("follow_up_sent_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("appointments", "follow_up_sent_at")
    op.drop_column("clinic_info", "follow_up_delay_days")
    op.drop_column("clinic_info", "follow_up_message")
