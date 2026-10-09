"""remember which language each caller actually speaks

Revision ID: 0014_spoken_language_memory
Revises: 0013_scheduled_job_runs
Create Date: F5

The voice agent already detected the caller's language per turn and
switched Sarvam TTS to match — and then threw it away when the call ended.
A patient who had called ten times in Telugu was greeted in English on the
eleventh, every time, because nothing persisted the detection.

Two columns, both nullable:

  patients.preferred_language   the language this patient last spoke.
                                Read by the agent on `patient_lookup` so a
                                returning caller is answered in it.

  call_logs.detected_language   what was spoken on THIS call. Kept
                                separately because the patient row holds
                                only the latest, and "which languages do
                                our callers use" is a question about calls
                                rather than people. It is also the
                                evidence for why a preference changed.

String(8) is enough for a BCP-47 tag of the form "en-IN". Bounded rather
than unbounded so the columns stay indexable later if a language
breakdown ever needs one — see
tests/test_indexable_columns_are_bounded.py.

Both are nullable with no backfill: there is no historical signal to
recover, and NULL correctly means "we have never heard this patient
speak". The agent treats NULL as "detect as usual", which is exactly the
pre-existing behaviour, so nothing changes for existing rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_spoken_language_memory"
down_revision: Union[str, None] = "0013_scheduled_job_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column("preferred_language", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "call_logs",
        sa.Column("detected_language", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("call_logs", "detected_language")
    op.drop_column("patients", "preferred_language")
