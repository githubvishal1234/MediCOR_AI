"""users: staff invitation columns

Revision ID: 0006_user_invitations
Revises: 0005_org_id_indexes
Create Date: HV-2

A clinic admin had no way to add a colleague: the only user-creating path
was POST /auth/register, which creates a whole new organization. HV-2 adds
an invite flow, and an invited user is a real `users` row from the moment
the invite is sent — created inactive, with an unusable password — rather
than living in a separate Invitation table. See the comment on
User.invite_* in app/models.py for why.

Only the SHA-256 of the invite token is stored, so a database read cannot
recover a working invite link. `invite_token_hash` is indexed because
POST /auth/accept-invite looks a user up by exactly that value, and it is
bounded at 64 characters (a hex SHA-256 digest) so SQL Server will accept
an index on it — a bare String would compile to VARCHAR(MAX), which
cannot be indexed.

All four columns are nullable, so this is additive and safe on a table
with existing rows: a pre-existing user simply has no invitation.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_user_invitations"
down_revision: Union[str, None] = "0005_org_id_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_INDEX = "ix_users_invite_token_hash"

_COLUMNS = [
    ("invite_token_hash", sa.String(length=64)),
    ("invited_at", sa.DateTime()),
    ("invite_expires_at", sa.DateTime()),
    ("invite_accepted_at", sa.DateTime()),
]


def _existing_columns() -> set:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns(_TABLE)}


def _existing_indexes() -> set:
    inspector = sa.inspect(op.get_bind())
    return {i["name"] for i in inspector.get_indexes(_TABLE) if i.get("name")}


def upgrade() -> None:
    # Guarded rather than strict: a database bootstrapped by the legacy
    # create_all() path (AUTO_CREATE_SCHEMA=true) already has these from
    # the model metadata, and this revision must be a no-op there.
    existing = _existing_columns()
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))

    if _INDEX not in _existing_indexes():
        op.create_index(_INDEX, _TABLE, ["invite_token_hash"])


def downgrade() -> None:
    if _INDEX in _existing_indexes():
        op.drop_index(_INDEX, table_name=_TABLE)

    existing = _existing_columns()
    for name, _type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
