"""audit_log: actor, target and request context (HV-1)

Revision ID: 0011_audit_log_context
Revises: 0010_call_evaluations
Create Date: HV-1

`audit_log` existed with four columns and was written by exactly two
things: `seed.py` and `ehr_service.py`. With a real write path it needs
the context `super_admin_audit_log` already carried — otherwise an entry
can say "a patient record was viewed" without saying which record, by
which account, or from where, which is not an audit trail.

Every column here is **nullable**, which means `database.sync_missing_
columns()` will add them at startup on the `AUTO_CREATE_SCHEMA=true` path
too. This revision exists so the Alembic path reaches the same schema,
not because the additive path cannot.

Two things deliberately NOT done:

* **`action` is left as `VARCHAR(MAX)`.** It is filtered on, and the
  house rule is that filterable columns get a length — but nothing
  indexes it, and `sync_missing_columns` alters nothing, so narrowing it
  here would leave the two schema paths disagreeing about the length of a
  column on every deployment that never runs Alembic. Not worth it until
  something needs the index.
* **No `(org_id, action)` index.** `ix_audit_log_org_occurred` (0005)
  already serves the ordering, and the action filter runs inside one
  org's rows. Adding a second index before there is a volume problem
  costs write throughput on a table that is now written on every request
  that touches PHI.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mssql

revision: str = "0011_audit_log_context"
down_revision: Union[str, None] = "0010_call_evaluations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "audit_log"

# (name, type) — all nullable, no server defaults. A pre-existing row
# legitimately has no actor or target: it was written before HV-1 by
# `seed.py` or `ehr_service.py`, and backfilling a guess would put
# invented attribution into an audit log, which is worse than a blank.
_COLUMNS = (
    ("actor_type", sa.String(length=16)),
    ("actor_user_id", sa.String(length=64)),
    ("target_type", sa.String(length=40)),
    ("target_id", sa.String(length=64)),
    ("detail", sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")),
    ("ip_address", sa.String(length=64)),
    ("user_agent", sa.String(length=400)),
)


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}
    for name, type_ in _COLUMNS:
        # Tolerant of the additive startup path having already added
        # these: on a machine running with AUTO_CREATE_SCHEMA=true, the
        # columns appear the first time the app boots, and this revision
        # may only be stamped afterwards.
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}
    for name, _type in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
