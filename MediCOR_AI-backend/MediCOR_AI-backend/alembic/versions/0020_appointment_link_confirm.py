"""patient self-service link and attendance confirmation

Revision ID: 0020_appointment_link_confirm
Revises: 0019_patient_contact_consent
Create Date: PF-06 / PF-08

Five columns on `appointments`, in two groups.

**The link (PF-06).**

  link_token_hash   SHA-256 of the token in the patient's link  String(64), indexed
  link_expires_at   when it stops working                       DateTime

Only the hash is stored, exactly as staff invitations do it: the plaintext
exists solely inside the link we send, so a database read cannot
reconstruct a working link. Indexed because resolving a link is an exact
match on this column and nothing else, and bounded at 64 because a
SHA-256 hex digest is 64 characters and an unbounded `String` is
VARCHAR(MAX) on SQL Server, which cannot be indexed at all.

It is deliberately NOT single-use, which is where it parts company with an
invite token. An invitation is accepted once; an appointment link has to
keep working until the appointment happens, because a patient will open
it, think, and come back. `link_expires_at` does the bounding instead, and
is set from the appointment's own start time -- the link's purpose ends
when the appointment does.

**The confirmation (PF-08).**

  confirmation_status  NULL | "confirmed"        String(16)
  confirmed_at         when they said so         DateTime
  confirmation_source  link | voice | staff      String(16)

There is deliberately no "declined" state: declining to attend IS
cancelling, and `status` already records that. Two fields expressing one
fact would eventually disagree.

This column was consciously left unbuilt until now. The reasoning is
recorded in the reminder work: nothing could write it, and a column
nothing writes is the `call_logs.recording_url` mistake -- a control that
looks like a capability and is not. PF-06 is its writer, which is what
makes adding it correct today and would have made it wrong before.

No backfill. NULL means the patient has not said, which is true of every
appointment that exists when this runs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_appointment_link_confirm"
down_revision: Union[str, None] = "0019_patient_contact_consent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments", sa.Column("link_token_hash", sa.String(length=64), nullable=True)
    )
    op.add_column("appointments", sa.Column("link_expires_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_appointments_link_token_hash", "appointments", ["link_token_hash"], unique=False
    )

    op.add_column(
        "appointments", sa.Column("confirmation_status", sa.String(length=16), nullable=True)
    )
    op.add_column("appointments", sa.Column("confirmed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "appointments", sa.Column("confirmation_source", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("appointments", "confirmation_source")
    op.drop_column("appointments", "confirmed_at")
    op.drop_column("appointments", "confirmation_status")
    op.drop_index("ix_appointments_link_token_hash", table_name="appointments")
    op.drop_column("appointments", "link_expires_at")
    op.drop_column("appointments", "link_token_hash")
