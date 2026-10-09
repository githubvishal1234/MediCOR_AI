"""what the patient tells us before the visit

Revision ID: 0027_pre_visit
Revises: 0026_waiting_list
Create Date: PF-13

Everything reception would otherwise collect at the desk while other
patients wait: what they are coming about, what they take, what they react
to, and whether their cover or contact details have changed.

**IT IS A SUBMISSION, NOT AN EDIT — and that is the whole design.**
Nothing here is written to `patients`. Two independent reasons, either
sufficient on its own:

  * **Security.** It is reached with the appointment-link token, which is
    unauthenticated by design (see routers/appointment_link.py). If that
    token could change a phone number, a link forwarded to a family group
    or lifted off a handset would become an account-takeover path:
    redirect the messages, then use inbound replies (PF-07) to cancel or
    book at will. A submission cannot be weaponised that way.

  * **Clinical correctness.** A patient-reported medication list is not a
    verified one. Writing it into a medical record because somebody typed
    it into a web form puts unverified clinical text where verified text
    is expected. A nurse reading it beforehand is the value; the record is
    updated by the person who checks it.

Hence `proposed_insurance_provider`, `proposed_insurance_member_id`,
`proposed_phone`, `proposed_email` — named so no future reader mistakes
them for the patient's actual details. The insurance pair keeps the
bounded lengths and the containment rules of `patients.insurance_*`: never
pushed to the EHR, never handed to the language model, never written into
an audit detail.

The free-text columns are `UNICODE_TEXT` (NVARCHAR(MAX) on SQL Server),
not `Text`. A patient may well write in Hindi or Telugu, and this schema
has already lost one set of transcripts to a non-Unicode column —
see 0012 and its repair in 0021.

One submission per appointment: a correction replaces the previous
answer rather than adding a second version for a nurse to reconcile at
the desk. Enforced in the router rather than by a UNIQUE constraint,
since the column would need to be nullable-unique and that is a dialect
trap this schema avoids.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0027_pre_visit"
down_revision: Union[str, None] = "0026_waiting_list"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NVARCHAR(MAX) on SQL Server, ordinary text elsewhere -- matching the
# UNICODE_TEXT variant used throughout app/models.py.
_TEXT = sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")


def upgrade() -> None:
    op.create_table(
        "pre_visit_submissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("appointment_id", sa.String(length=64), nullable=False),
        sa.Column("patient_id", sa.String(length=64), nullable=True),
        sa.Column("visit_reason", _TEXT, nullable=True),
        sa.Column("medications", _TEXT, nullable=True),
        sa.Column("allergies", _TEXT, nullable=True),
        sa.Column("notes", _TEXT, nullable=True),
        sa.Column("proposed_insurance_provider", sa.String(length=120), nullable=True),
        sa.Column("proposed_insurance_member_id", sa.String(length=64), nullable=True),
        sa.Column("proposed_phone", sa.String(length=30), nullable=True),
        sa.Column("proposed_email", sa.String(length=200), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pre_visit_org_appointment",
        "pre_visit_submissions",
        ["org_id", "appointment_id"],
    )
    op.create_index(
        "ix_pre_visit_submissions_appointment_id",
        "pre_visit_submissions",
        ["appointment_id"],
    )
    op.create_index(
        "ix_pre_visit_submissions_patient_id", "pre_visit_submissions", ["patient_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_pre_visit_submissions_patient_id", table_name="pre_visit_submissions")
    op.drop_index(
        "ix_pre_visit_submissions_appointment_id", table_name="pre_visit_submissions"
    )
    op.drop_index("ix_pre_visit_org_appointment", table_name="pre_visit_submissions")
    op.drop_table("pre_visit_submissions")
