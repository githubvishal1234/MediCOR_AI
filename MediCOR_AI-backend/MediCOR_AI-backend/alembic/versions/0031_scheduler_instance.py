"""record which process won each scheduler claim

Revision ID: 0031_scheduler_instance
Revises: 0030_webhook_retry
Create Date: GAP-T1

This deployment cannot run more than one process of the API safely --
`rate_limit._attempts` is a module-level dict and
`realtime.ConnectionManager` holds WebSockets in memory, so a second
worker gets its own copy of both. A login limit of 5 becomes 10, and a
dashboard connected to one worker never sees events broadcast by the
other. Neither failure raises. Neither looks broken on screen. That is
the whole problem: it is discovered months later, by someone wondering
why a rate limit did not hold.

`app/instance.py` catches the common shape of this (`--workers 4`,
`WEB_CONCURRENCY`) at startup by reading the process's own command line.
What it cannot see from there is two processes started *separately* --
two terminals, two containers, a stale server nobody stopped -- each of
which honestly reports one worker.

This column closes that. `scheduled_job_runs` is already the one table
every process writes to, and the UNIQUE constraint on
(job_name, run_key) already makes exactly one of them the winner of each
tick. Stamping the winner's instance id on the row means the losers can
read who beat them, and a *different* id is direct evidence of a second
process rather than an inference from behaviour.

Nullable, and it must stay nullable: rows written before this migration
have no answer, and a job must never fail to run because its
bookkeeping column is empty. `String(64)` rather than unbounded because
this is a short opaque id and an unbounded String is VARCHAR(MAX) on SQL
Server -- see tests/test_indexable_columns_are_bounded.py. Not indexed:
it is read only on the losing side of a claim, which has already located
the row by the unique key.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_scheduler_instance"
down_revision: Union[str, None] = "0030_webhook_retry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scheduled_job_runs",
        sa.Column("instance_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_job_runs", "instance_id")
