"""call_logs: LLM analysis columns

Revision ID: 0008_llm_call_analysis
Revises: 0007_notifications_and_callbacks
Create Date: AI-2

`ai_summary`, `outcome` and `sentiment` already existed and are still the
fields the dashboard reads — AI-2 overwrites them in place, which is why
no UI code had to change. These four columns record what the model added
and, crucially, *where the values came from*.

`summary_model` being null means the row holds the rule-based fallback
from app/call_intelligence.py rather than a model-generated summary. That
distinction matters: the UI has always labelled the field "AI Summary"
while it was in fact an f-string template, and this is what lets it stop
doing that.

All four are nullable and nothing is backfilled, so every existing call
keeps exactly the values it has today.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_llm_call_analysis"
down_revision: Union[str, None] = "0007_notifications_and_callbacks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "call_logs"
_COLUMNS = [
    # Bounded: a short phrase ("book appointment"), and a candidate for
    # grouping in the HV-9 analytics work, which needs it indexable.
    ("intent", sa.String(length=120)),
    ("extracted_json", sa.Text()),
    ("summary_model", sa.String(length=120)),
    ("summary_generated_at", sa.DateTime()),
]


def _existing() -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    existing = _existing()
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    existing = _existing()
    for name, _type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
