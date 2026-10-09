"""call_evaluations: automated call quality scores

Revision ID: 0010_call_evaluations
Revises: 0009_kb_chunks
Create Date: AI-7

There was no way to tell whether a change to the agent made it better or
worse. Transcripts and `call_logs.actions_taken` already provided the
corpus; only the scoring was missing.

One row per (call, judge_model) rather than one per call: re-running the
judge after changing the rubric should produce a comparable second
opinion, not silently overwrite the first. `call_id` is indexed because
the call-detail view looks up by it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mssql

revision: str = "0010_call_evaluations"
down_revision: Union[str, None] = "0009_kb_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "call_evaluations"
_IX_ORG = "ix_call_evaluations_org_evaluated"
_IX_CALL = "ix_call_evaluations_call_id"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("org_id", sa.String(length=64), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("call_id", sa.String(length=64), sa.ForeignKey("call_logs.id"), nullable=False),
            sa.Column("score", sa.Integer(), nullable=True),
            sa.Column("goal_achieved", sa.Boolean(), nullable=True),
            sa.Column("hallucination_detected", sa.Boolean(), nullable=True),
            sa.Column("policy_violations_json", sa.Text(), nullable=True),
            sa.Column("conciseness", sa.Integer(), nullable=True),
            sa.Column(
                "reasoning",
                sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql"),
                nullable=True,
            ),
            sa.Column("judge_model", sa.String(length=120), nullable=True),
            sa.Column("evaluated_at", sa.DateTime(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        )

    existing = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(_TABLE) if i.get("name")}
    if _IX_ORG not in existing:
        op.create_index(_IX_ORG, _TABLE, ["org_id", "evaluated_at"])
    if _IX_CALL not in existing:
        op.create_index(_IX_CALL, _TABLE, ["call_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE in set(inspector.get_table_names()):
        existing = {i["name"] for i in inspector.get_indexes(_TABLE) if i.get("name")}
        for ix in (_IX_CALL, _IX_ORG):
            if ix in existing:
                op.drop_index(ix, table_name=_TABLE)
        op.drop_table(_TABLE)
