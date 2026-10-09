"""phone_numbers: map a dialled number to the clinic that owns it

Revision ID: 0004_phone_numbers
Revises: 0003_organization_timezone
Create Date: Phase 2

Nothing in the schema previously connected a telephone number to an
organization, so the voice-agent worker could only ever serve one clinic
— its .env held a single BACKEND_API_KEY and every caller on every number
wrote into that org.

This table is what lets one deployment answer for many clinics:
POST /agent/resolve-tenant looks the dialled number up here and hands the
agent a short-lived, org-scoped token.

`e164` is globally unique. A number belongs to exactly one clinic, and
resolution must answer "who owns this?" without an org in hand.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_phone_numbers"
down_revision: Union[str, None] = "0003_organization_timezone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "phone_numbers"


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if _TABLE in _tables():
        # Already created by the legacy create_all()/sync bootstrap.
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("org_id", sa.String(64), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("e164", sa.String(32), nullable=False),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("trunk_id", sa.String(120), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_phone_numbers_e164", _TABLE, ["e164"], unique=True)
    op.create_index("ix_phone_numbers_org_id", _TABLE, ["org_id"])


def downgrade() -> None:
    if _TABLE in _tables():
        op.drop_table(_TABLE)
