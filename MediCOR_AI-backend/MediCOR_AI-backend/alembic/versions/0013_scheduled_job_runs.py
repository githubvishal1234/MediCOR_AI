"""scheduled_job_runs: the lock that makes in-process scheduling safe

Revision ID: 0013_scheduled_job_runs
Revises: 0012_unicode_text_columns
Create Date: F2

Nothing in this application could happen on a schedule: every background
task was a FastAPI `BackgroundTasks` callback, which only runs because a
request arrived. This deployment is SQL Server Express — no SQL Agent, no
cron, no separate worker — so the scheduler has to live in the API
process.

The interesting part of that is not the timer, it is this table.

`rate_limit._attempts` and `realtime.ConnectionManager` already make a
second uvicorn worker misbehave silently, and an in-memory scheduler would
have added a third symptom: each worker firing its own timer, so every
clinic gets two of every reminder with nothing raising an error.

The UNIQUE constraint on (job_name, run_key) is the fix. A job is claimed
by INSERTing its row for the current time bucket; two processes racing the
same tick both attempt it, exactly one commits, and the loser takes an
IntegrityError and skips. The database is the leader election — no Redis,
no advisory lock, no new dependency.

Both constrained columns are bounded (`String(120)`), because SQL Server
cannot put a UNIQUE constraint on VARCHAR(MAX). That is the failure
`tests/test_phase5_plan_column.py` was written for and
`tests/test_indexable_columns_are_bounded.py` now guards against.

`detail` is NVARCHAR(MAX) rather than TEXT — see migration 0012.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0013_scheduled_job_runs"
down_revision: Union[str, None] = "0012_unicode_text_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "scheduled_job_runs"
_IX_JOB = "ix_scheduled_job_runs_job_name"
_UQ = "uq_scheduled_job_runs_job_key"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("job_name", sa.String(length=120), nullable=False),
        sa.Column("run_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "detail",
            sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql"),
            nullable=True,
        ),
        sa.UniqueConstraint("job_name", "run_key", name=_UQ),
    )
    op.create_index(_IX_JOB, _TABLE, ["job_name"])


def downgrade() -> None:
    op.drop_index(_IX_JOB, table_name=_TABLE)
    op.drop_table(_TABLE)
