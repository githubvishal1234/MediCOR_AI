"""webhooks: make the outbound webhook actually deliver

Revision ID: 0018_webhook_delivery
Revises: 0017_appt_reminder_sent
Create Date: F14

The `webhooks` table, its API, the config screen on the EHR Integration
page and a landing-page bullet reading "Webhooks for custom
infrastructure" all existed. Nothing ever delivered anything:
`endpoint_url` was written by the clinic and then only ever read back out
in the same endpoint's response.

The three events it offered were fictional too — no part of the codebase
has ever emitted `transcript.completed`, `intent.requires_action` or
`agent.error`. So the default is rewritten to events that actually
happen, but ONLY where the row still holds that exact fictional string:
a clinic that customised its selection kept a choice, however unusable,
and silently rewriting it would be the more surprising of the two.

Three columns:

  secret                 HMAC-SHA256 signing key. Stored recoverable,
                         unlike APIKey.hashed_key — signing needs the
                         plaintext on this side and the clinic needs the
                         same value on theirs. Hashing it would make
                         signing impossible rather than safer. Left NULL
                         here and minted lazily on first read, so a clinic
                         that never uses webhooks carries no secret.

  last_delivery_at       When we last tried, UTC.
  last_delivery_status   What happened ("204", "timeout", "error: ...").

The last two live on the row rather than in a delivery-log table on
purpose: one line of truth for "is this working?" without a row per event
for every call a busy clinic takes, which is the volume that would bury
the audit log.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_webhook_delivery"
down_revision: Union[str, None] = "0017_appt_reminder_sent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_DEFAULT = "transcript.completed,intent.requires_action,agent.error"
_NEW_DEFAULT = "call.completed,appointment.booked,appointment.cancelled"


def upgrade() -> None:
    op.add_column("webhooks", sa.Column("secret", sa.String(length=64), nullable=True))
    op.add_column("webhooks", sa.Column("last_delivery_at", sa.DateTime(), nullable=True))
    op.add_column("webhooks", sa.Column("last_delivery_status", sa.String(length=120), nullable=True))

    webhooks = sa.table(
        "webhooks",
        sa.column("events_csv", sa.String),
    )
    op.execute(
        webhooks.update()
        .where(webhooks.c.events_csv == op.inline_literal(_OLD_DEFAULT))
        .values(events_csv=op.inline_literal(_NEW_DEFAULT))
    )


def downgrade() -> None:
    op.drop_column("webhooks", "last_delivery_status")
    op.drop_column("webhooks", "last_delivery_at")
    op.drop_column("webhooks", "secret")
