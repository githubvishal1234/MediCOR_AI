"""remove insurance entirely - this product does not handle it

Revision ID: 0034_drop_insurance
Revises: 0033_call_provider_sid
Create Date: insurance removal

Drops six columns across three tables:

  patients.insurance_provider           String(120)
  patients.insurance_member_id          String(64)
  patients.insurance_group_number       String(64)
  clinic_info.insurance_info            NVARCHAR(MAX)
  pre_visit_submissions.proposed_insurance_provider   String(120)
  pre_visit_submissions.proposed_insurance_member_id  String(64)

WHY THIS IS A DROP AND NOT A DEPRECATION

The product does not handle insurance, so every one of these is a field
the application no longer reads or writes. This codebase has a name for
that shape -- `call_logs.recording_url`, a column nothing ever wrote
behind a "Play Recording" button that could never work -- and treats it
as a defect rather than harmless spare capacity. Leaving six of them,
one of which the voice agent used to read aloud to callers, would be the
same mistake six times over.

WHAT WAS CHECKED FIRST

Nothing real is lost. Before this ran:

  * **0 of 7 patients** held any value in any of the three patient
    columns. Not "few" -- none.
  * **0 pre-visit submissions** held either proposed value.
  * **1 clinic_info row** held `insurance_info`, and its content was the
    US demo text seeded by app/seed.py ("We accept Aetna, Blue Cross
    Blue Shield, Cigna, UnitedHealthcare and Medicare"), which described
    no real clinic in this deployment.

That check is the reason this is a drop. Had a single patient carried a
real member id, the honest move would have been to export it first and
say so, because `downgrade()` below restores the columns and cannot
restore what was in them.

WHAT THE DOWNGRADE IS AND IS NOT

It re-creates all six columns, nullable and empty. It is a schema
rollback, not a data one: re-applying the old application code against a
downgraded database gives you working, blank insurance fields. That is
the most an irreversible drop can offer, and stating it here is better
than implying otherwise.

SQL SERVER NOTE

None of the six carries an index, a default, or a constraint, so a plain
`DROP COLUMN` is enough. Had any of them had a default, SQL Server would
require the auto-named constraint to be dropped first -- it refuses to
drop a column a constraint still references, and the constraint's name is
generated, so it cannot be written literally here.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0034_drop_insurance"
down_revision: Union[str, None] = "0033_call_provider_sid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same variant the rest of the schema uses for free text: NVARCHAR(MAX)
# on SQL Server, portable to the SQLite the tests run on. Only needed by
# `downgrade`, which has to put back exactly what was removed.
_TEXT = sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")


def upgrade() -> None:
    op.drop_column("patients", "insurance_provider")
    op.drop_column("patients", "insurance_member_id")
    op.drop_column("patients", "insurance_group_number")
    op.drop_column("clinic_info", "insurance_info")
    op.drop_column("pre_visit_submissions", "proposed_insurance_provider")
    op.drop_column("pre_visit_submissions", "proposed_insurance_member_id")


def downgrade() -> None:
    op.add_column(
        "patients", sa.Column("insurance_provider", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "patients", sa.Column("insurance_member_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "patients",
        sa.Column("insurance_group_number", sa.String(length=64), nullable=True),
    )
    op.add_column("clinic_info", sa.Column("insurance_info", _TEXT, nullable=True))
    op.add_column(
        "pre_visit_submissions",
        sa.Column("proposed_insurance_provider", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "pre_visit_submissions",
        sa.Column("proposed_insurance_member_id", sa.String(length=64), nullable=True),
    )
