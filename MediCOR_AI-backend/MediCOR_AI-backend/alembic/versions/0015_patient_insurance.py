"""per-patient insurance cover

Revision ID: 0015_patient_insurance
Revises: 0014_spoken_language_memory
Create Date: F12

The clinic could already publish which insurers IT accepts
(`clinic_info.insurance_info`, which the voice agent reads out when a
caller asks "do you take my insurance?"). The other half was missing:
which cover THIS patient actually holds. Staff had nowhere to record what
a caller told them, so the answer to that question could only ever be
generic.

Three bounded columns, all nullable:

  insurance_provider      e.g. "Star Health"        String(120)
  insurance_member_id     the member/policy number  String(64)
  insurance_group_number  group/corporate plan id   String(64)

Bounded rather than unbounded because these are identifiers, not free
text, and `Column(String)` is VARCHAR(MAX) on SQL Server — unindexable,
which a "patients on plan X" report would eventually want. See
tests/test_indexable_columns_are_bounded.py.

Two things deliberately NOT done here:

  * They are not added to the EHR sync payload. `ehr_service` pushes
    mrn/name/dob/phone/status to a third party; sending a member id there
    is its own decision about what leaves this system, not a side effect
    of storing it.

  * They are not exposed on `PatientListOut`. That lean shape is what the
    voice agent's `patient_lookup` returns straight into a live LLM
    prompt, and a member id has no business in one.

No backfill: NULL correctly means "we have not been told".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_patient_insurance"
down_revision: Union[str, None] = "0014_spoken_language_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("patients", sa.Column("insurance_provider", sa.String(length=120), nullable=True))
    op.add_column("patients", sa.Column("insurance_member_id", sa.String(length=64), nullable=True))
    op.add_column("patients", sa.Column("insurance_group_number", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("patients", "insurance_group_number")
    op.drop_column("patients", "insurance_member_id")
    op.drop_column("patients", "insurance_provider")
