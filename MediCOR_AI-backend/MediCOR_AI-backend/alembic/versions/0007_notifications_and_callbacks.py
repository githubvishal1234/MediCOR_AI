"""notifications table + missed-call callback columns

Revision ID: 0007_notifications_and_callbacks
Revises: 0006_user_invitations
Create Date: HV-6 / HV-7

Two changes that share a migration because both are additive and both
land in the same release.

**HV-6 — `notifications`.** `realtime.notify()` broadcast over WebSocket
only and stored nothing, so a page refresh wiped the list and anything
raised while nobody had the dashboard open was lost for good. Persisting
in `notify()` (the single choke point every notification already flowed
through) means no router that raises one had to change.

**HV-7 — `call_logs.callback_*`.** `status` already supported "failed"
and "no_answer" and `update_call` already fired a warning for them, but
nothing recorded what happened next, so a missed call was a transient
toast. Three nullable columns turn it into a work item. Null means "not
applicable", so every existing row and every answered call is untouched.

Both are safe on a populated database: one new table, three nullable
columns, no backfill, no type changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_notifications_and_callbacks"
down_revision: Union[str, None] = "0006_user_invitations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIF_TABLE = "notifications"
_NOTIF_INDEX = "ix_notifications_org_created"

_CALLS_TABLE = "call_logs"
_CALLBACK_COLUMNS = [
    ("callback_status", sa.String(length=16)),
    ("callback_note", sa.Text()),
    ("callback_updated_at", sa.DateTime()),
]


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()

    # --- HV-6 ---
    if _NOTIF_TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _NOTIF_TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "org_id",
                sa.String(length=64),
                sa.ForeignKey("organizations.id"),
                nullable=False,
            ),
            sa.Column("level", sa.String(length=16), nullable=True),
            # Bounded: SQL Server cannot index or constrain VARCHAR(MAX),
            # and 200 matches the NotificationOut contract.
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("data_json", sa.Text(), nullable=True),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    inspector = _inspector()
    if _NOTIF_INDEX not in {
        i["name"] for i in inspector.get_indexes(_NOTIF_TABLE) if i.get("name")
    }:
        # Serves both the newest-first inbox page and the unread badge
        # count, each scoped to one org.
        op.create_index(_NOTIF_INDEX, _NOTIF_TABLE, ["org_id", "created_at"])

    # --- HV-7 ---
    existing = {c["name"] for c in _inspector().get_columns(_CALLS_TABLE)}
    for name, type_ in _CALLBACK_COLUMNS:
        if name not in existing:
            op.add_column(_CALLS_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    existing = {c["name"] for c in _inspector().get_columns(_CALLS_TABLE)}
    for name, _type in reversed(_CALLBACK_COLUMNS):
        if name in existing:
            op.drop_column(_CALLS_TABLE, name)

    inspector = _inspector()
    if _NOTIF_TABLE in set(inspector.get_table_names()):
        if _NOTIF_INDEX in {
            i["name"] for i in inspector.get_indexes(_NOTIF_TABLE) if i.get("name")
        }:
            op.drop_index(_NOTIF_INDEX, table_name=_NOTIF_TABLE)
        op.drop_table(_NOTIF_TABLE)
