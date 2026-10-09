"""bounded retry for failed webhook deliveries

Revision ID: 0030_webhook_retry
Revises: 0029_password_reset
Create Date: GAP-T4

F14 shipped one delivery attempt and said plainly why: retrying needs
somewhere durable to hold the attempt, and a per-event delivery table
would collect a row for every call a busy clinic takes. It also named the
fix -- "if retries are wanted, the scheduler (F2) is the place, its claim
row already solves the double-send problem that makes retries dangerous."
The scheduler now exists, so this is that deferred decision being taken.

**Only failures get a row**, which is what answers the original
objection. A delivery that succeeds first time writes nothing here; the
outcome still goes on `webhooks.last_delivery_status` exactly as before.
The table is therefore empty for a clinic whose endpoint is healthy, and
holds a short backlog for one that is briefly down. Settled rows are
pruned after `WEBHOOK_DELIVERY_RETENTION_DAYS`.

**`body` is the exact JSON that was sent, stored verbatim**, for two
reasons that both matter:

  * the signature is an HMAC over the bytes on the wire, so a
    re-serialised dict with different key order would fail verification
    (see `webhook_service.sign`);

  * the payload carries its own `delivery_id`, and a retry has to reuse
    it -- otherwise a receiver cannot tell the third attempt from a third
    event, and deduplication is impossible.

The signature itself is deliberately NOT stored: it is recomputed at send
time, so a clinic that rotates its secret between attempts still receives
something it can verify.

`UNICODE_TEXT` for the body: it is JSON that can contain a patient's name
or a medication written in Hindi or Telugu, and this schema has already
lost one set of transcripts to a non-Unicode column (0012, repaired in
0021).

Indexed on (status, next_attempt_at) -- the query the drain job runs every
tick is "what is pending and due".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0030_webhook_retry"
down_revision: Union[str, None] = "0029_password_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEXT = sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("webhook_id", sa.String(length=64), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=True),
        sa.Column("body", _TEXT, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_webhook_deliveries_due", "webhook_deliveries", ["status", "next_attempt_at"]
    )
    op.create_index("ix_webhook_deliveries_org_id", "webhook_deliveries", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_org_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
