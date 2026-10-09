"""record which telephony provider carried a call, and its own id for it

Revision ID: 0033_call_provider_sid
Revises: 0032_rule_conditions
Create Date: Twilio + Deepgram voice stack migration

  call_logs.provider          String(32), nullable   -- "twilio" | "livekit"
  call_logs.provider_call_sid String(64), nullable   -- Twilio CallSid, etc.

WHY THIS ARRIVES WITH THE VOICE MIGRATION

Until now a call log held no reference to the call as the *carrier* knows
it. That was survivable while one stack existed and every question could
be answered from this database alone. It stops being survivable the
moment two stacks run side by side: "did this call come through the new
gateway or the old worker?" had no answer in the data, only in whichever
process happened to write the row.

`provider_call_sid` is the join key to the carrier's own record --
Twilio's CallSid is what their console, their logs and their billing are
indexed by, so a support question that starts in this dashboard can be
finished in theirs. Without it, correlating a complaint about one call
with the carrier's view of it means guessing from timestamps.

WHY BOTH COLUMNS AND NOT ONE

An id with no provider is ambiguous the day a second carrier exists, and
this project has already changed carrier once (Vobiz -> Twilio). Storing
the namespace beside the identifier costs 32 characters and removes the
guess.

BOUNDED, BECAUSE THESE ARE IDENTIFIERS

`String` with no length compiles to VARCHAR(MAX) on SQL Server, which
cannot be indexed or made unique -- and "find the call log for CallSid X"
is exactly the lookup this column exists to serve. 34 characters is a
Twilio SID; 64 leaves room for a provider that is more generous.
Deliberately NOT indexed or unique yet: nothing queries by it today, an
index on a column with one writer is speculation, and
`tests/test_indexable_columns_are_bounded.py` guards the direction that
actually matters (an index appearing on an *unbounded* column).

No backfill. NULL on every existing row is honest: those calls were
carried by the LiveKit/SIP path, which never recorded a carrier id, and
inventing "livekit" for them would assert a fact nobody checked.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_call_provider_sid"
down_revision: Union[str, None] = "0032_rule_conditions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("call_logs", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column(
        "call_logs", sa.Column("provider_call_sid", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("call_logs", "provider_call_sid")
    op.drop_column("call_logs", "provider")
