"""give escalation rules a condition the server can evaluate

Revision ID: 0032_rule_conditions
Revises: 0031_scheduler_instance
Create Date: GAP-T9

An escalation rule was a title and a sentence of prose. Both were
rendered into the agent's system prompt, and whether a rule fired was
whether the model recognised it -- so "transfer anyone mentioning chest
pain" was a suggestion, evaluated afresh by a language model on every
call, with no record of whether it was applied.

That was documented rather than hidden (`escalation_rules_for` says so,
and says the fix "needs a real `keywords` column first, so it is
deliberately not faked out of this prose"). These are those columns.

WHAT THE BACKFILL DOES, AND WHY IT MATTERS MOST

Every existing row becomes `condition_type='model'`, which is exactly
what it already was. Applying this migration changes no clinic's
behaviour: the rules keep going into the prompt and the model keeps
matching them. A clinic gets deterministic evaluation only by choosing
a condition type for a rule, one rule at a time. A migration that
silently converted prose rules into keyword rules would have changed
what a live phone line does to callers, on deploy, with nobody asking
for it.

`model` therefore stays a first-class member of the vocabulary, not a
legacy value. Some rules genuinely cannot be structured -- "transfer if
the caller sounds distressed" is not a keyword -- and pretending
otherwise would push clinics into writing bad keyword lists for
conditions that need judgement.

THE COLUMNS

`keywords` is UNICODE_TEXT, not String: the whole reason keyword
matching was resisted is that a caller saying the Hindi or Telugu for
"chest pain" must be able to match, which means a clinic types Devanagari
and Telugu script into this column. This schema has already lost one set
of transcripts to a non-Unicode column (0012, repaired in 0021).

`destination` is bounded at 64 -- a phone number or an extension, not
prose -- and nullable, meaning "the default transfer number". `priority`
orders evaluation so the first match is a defined one rather than
whichever row the database returned first.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0032_rule_conditions"
down_revision: Union[str, None] = "0031_scheduler_instance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEXT = sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")


def upgrade() -> None:
    op.add_column(
        "routing_rules",
        sa.Column("condition_type", sa.String(length=24), nullable=True),
    )
    op.add_column("routing_rules", sa.Column("keywords", _TEXT, nullable=True))
    op.add_column(
        "routing_rules", sa.Column("destination", sa.String(length=64), nullable=True)
    )
    op.add_column("routing_rules", sa.Column("priority", sa.Integer(), nullable=True))

    # Existing rules keep behaving exactly as they do today. See above:
    # this migration must not change what any live phone line does.
    op.execute(
        "UPDATE routing_rules SET condition_type = 'model' "
        "WHERE condition_type IS NULL"
    )
    op.execute("UPDATE routing_rules SET priority = 100 WHERE priority IS NULL")


def downgrade() -> None:
    op.drop_column("routing_rules", "priority")
    op.drop_column("routing_rules", "destination")
    op.drop_column("routing_rules", "keywords")
    op.drop_column("routing_rules", "condition_type")
