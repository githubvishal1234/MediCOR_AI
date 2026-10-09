"""self-service password reset

Revision ID: 0029_password_reset
Revises: 0028_follow_up
Create Date: GAP-U1

  users.reset_token_hash     SHA-256 of the token in the link   String(64), indexed
  users.reset_expires_at     when it stops working              DateTime
  users.reset_requested_at   when it was asked for              DateTime

Before this there was no self-service path at all: a locked-out staff
member needed an administrator or a direct database edit.

Same construction as the invite token three columns above -- only the
hash is stored, so a database read cannot mint a working link, and lookup
is an exact match on an indexed 64-character digest.

It differs from an invite in two deliberate ways. It is **short-lived**
(an hour, versus days) and **strictly single-use**: a reset token is a
bearer credential for an existing account, where an invitation is a
welcome. `reset_token_hash` is cleared on use, so a spent link is
indistinguishable from one that never existed -- the same reason
`accept-invite` returns one identical error for unknown, expired and
already-accepted.

`reset_requested_at` is kept after the token is cleared. "Somebody asked"
and "the password changed" are separate facts, recorded under separate
audit codes, and a flood of the first with none of the second is somebody
probing addresses.

**A known limitation, recorded here rather than discovered later:**
resetting a password does not invalidate existing sessions. The JWTs this
product issues are stateless and carry no revocation list, so a token
already in someone's hands stays valid until it expires. Fixing that
means a database lookup on every request, which is an architectural
decision and not one to make inside a reset endpoint.

No backfill: NULL means no reset is outstanding, true of every row.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_password_reset"
down_revision: Union[str, None] = "0028_follow_up"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("reset_token_hash", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("reset_expires_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("reset_requested_at", sa.DateTime(), nullable=True))
    op.create_index("ix_users_reset_token_hash", "users", ["reset_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_users_reset_token_hash", table_name="users")
    op.drop_column("users", "reset_requested_at")
    op.drop_column("users", "reset_expires_at")
    op.drop_column("users", "reset_token_hash")
