"""refill_requests: repeat prescriptions asked for by phone

Revision ID: 0016_refill_requests
Revises: 0015_patient_insurance
Create Date: F9

Refill calls are high volume and low clinical risk *as requests*. The
agent's only previous option was to transfer, so a receptionist took the
same message by hand — recording it is strictly better than transcribing
it twice, provided the decision stays with a person.

Its own table rather than a status on `prescriptions`, because "the
patient asked" and "the clinic agreed" are different facts and only the
second carries clinical weight. Collapsing them would make an AI-recorded
request indistinguishable from a prescriber's approval, which is the one
mistake this feature must not make.

`medication` is free text: it is what the caller SAID, not a validated
drug code. `prescription_id` is filled only on a case-insensitive exact
match against something already on that patient's record — a link when
there is one, honestly absent when there is not, rather than a fuzzy
guess presented as a match.

The index is (org_id, status, requested_at), which is exactly how the
queue is read: this clinic's outstanding requests, oldest first. `status`
is String(16) rather than unbounded because it is part of that index and
SQL Server cannot index VARCHAR(MAX) — see
tests/test_indexable_columns_are_bounded.py.

`note` is NVARCHAR(MAX) rather than TEXT; see migration 0012.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0016_refill_requests"
down_revision: Union[str, None] = "0015_patient_insurance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "refill_requests"
_IX_ORG = "ix_refill_requests_org_id"
_IX_QUEUE = "ix_refill_requests_org_status"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("org_id", sa.String(length=64), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("patient_id", sa.String(length=64), sa.ForeignKey("patients.id"), nullable=True),
        sa.Column("prescription_id", sa.String(length=64), sa.ForeignKey("prescriptions.id"), nullable=True),
        sa.Column("call_id", sa.String(length=64), sa.ForeignKey("call_logs.id"), nullable=True),
        sa.Column("medication", sa.String(length=200), nullable=False),
        sa.Column(
            "note",
            sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column("handled_by_user_id", sa.String(length=64), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index(_IX_ORG, _TABLE, ["org_id"])
    op.create_index(_IX_QUEUE, _TABLE, ["org_id", "status", "requested_at"])


def downgrade() -> None:
    op.drop_index(_IX_QUEUE, table_name=_TABLE)
    op.drop_index(_IX_ORG, table_name=_TABLE)
    op.drop_table(_TABLE)
