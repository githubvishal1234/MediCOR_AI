"""remember that a caller asked to be spoken to more slowly

Revision ID: 0022_speech_pace
Revises: 0021_unicode_text_repair
Create Date: PF-16

Two columns, in the same relationship to each other as the spoken-language
pair added by `0014`:

  patients.speech_pace_preference   the standing preference   String(16)
  call_logs.speech_pace_requested   the evidence, per call    String(16)

"slow" or NULL. Never a number, even though the agent could just as
easily report a pace figure -- that value goes straight back into Sarvam
TTS on this patient's next call, and an out-of-range one would either be
rejected mid-call or produce something unintelligible. The vocabulary is
closed in `schemas.SPEECH_PACE_PREFERENCES` and the actual figure lives in
the agent's configuration, which keeps "what the patient asked for" and
"what we send the vendor" as separate concerns.

**Only ever set because the patient asked.** Nothing infers it from age,
from how long their calls run, or from how often they ask for repetition.
Being slowed down unbidden is its own small insult, and a system that
guesses at disability will guess wrong in public.

Unlike the language preference, this one is **sticky rather than
last-wins**: someone who needed it slower once is unlikely to need it
faster later. `routers/calls.py` therefore sets it only when it is not
already set, and staff can clear it on the patient record if that turns
out to be wrong for someone.

Bounded at 16 for the usual reason on this deployment: an unbounded
`String` is VARCHAR(MAX) on SQL Server and cannot be indexed, and "which
of our patients need this" is a plausible thing for a clinic to want to
filter on later.

No backfill. NULL means the patient has never asked, which is true of
everyone when this runs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_speech_pace"
down_revision: Union[str, None] = "0021_unicode_text_repair"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patients", sa.Column("speech_pace_preference", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "call_logs", sa.Column("speech_pace_requested", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("call_logs", "speech_pace_requested")
    op.drop_column("patients", "speech_pace_preference")
