"""index org_id on every tenant-scoped table

Revision ID: 0005_org_id_indexes
Revises: 0004_phone_numbers
Create Date: Phase 1 (#7)

Every tenant-scoped query in this codebase filters on `org_id` — that is
the whole isolation model (see app/deps.py's OrgContext and the
`_get_*_or_404` helpers in each router). Until this revision there was no
index on it anywhere: the schema had exactly three indexed columns
(`users.email`, `super_admins.email`, `phone_numbers.e164`), all of them
incidental to a UNIQUE constraint.

So every list endpoint — patients, calls, appointments, audit log —
performed a full table scan filtered in memory, on tables that grow
without bound as call volume increases. The cost is invisible on the
seeded demo org and becomes the dominant query cost at real volume.

Three composite indexes are added alongside the single-column ones, for
the query shapes that actually appear in the routers:

  * call_logs (org_id, occurred_at)  — dashboard stats and call volume
    both filter by org AND a clinic-local day range.
  * appointments (org_id, start_at)  — the calendar orders by start_at
    within an org; this also serves _validate_slot's conflict check.
  * audit_log (org_id, occurred_at)  — GET /security/audit-log orders by
    occurred_at desc within an org.

A composite (org_id, X) index also serves a lookup on org_id alone, so
those three tables deliberately get only the composite, not both.

Tables whose `org_id` carries a UNIQUE constraint (clinic_info,
agent_settings, webhooks, subscriptions) are skipped — SQL Server already
backs that constraint with an index.

Idempotent in both directions: each index is created only if absent and
dropped only if present, so this is safe on a database that was built by
the legacy create_all() path with the updated models already in place.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_org_id_indexes"
down_revision: Union[str, None] = "0004_phone_numbers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, [columns]) — index_name matches SQLAlchemy's default
# `ix_<table>_<column>` convention for the single-column entries, so a
# fresh database built by create_all() from the updated models produces
# identical names and this revision is a no-op there.
_SINGLE_COLUMN_TABLES = [
    "users",
    "patients",
    "doctors",
    "pending_bookings",
    "phone_numbers",
    "kb_documents",
    "kb_sources",
    "faqs",
    "services",
    "routing_rules",
    "ehr_integrations",
    "api_keys",
    "invoices",
    "support_tickets",
]

_COMPOSITE_INDEXES = [
    ("ix_call_logs_org_occurred", "call_logs", ["org_id", "occurred_at"]),
    ("ix_appointments_org_start", "appointments", ["org_id", "start_at"]),
    ("ix_audit_log_org_occurred", "audit_log", ["org_id", "occurred_at"]),
]


def _all_indexes() -> list[tuple[str, str]]:
    """Every (table, index_name) pair, for the create/drop guards."""
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    pairs: list[tuple[str, str]] = []
    for table in existing_tables:
        for index in inspector.get_indexes(table):
            if index.get("name"):
                pairs.append((table, index["name"]))
    return pairs


def _planned() -> list[tuple[str, str, list[str]]]:
    planned = [
        (f"ix_{table}_org_id", table, ["org_id"]) for table in _SINGLE_COLUMN_TABLES
    ]
    planned.extend(_COMPOSITE_INDEXES)
    return planned


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    existing = set(_all_indexes())

    for index_name, table, columns in _planned():
        if table not in existing_tables:
            # Table not created yet in this environment; create_all() or a
            # later revision will bring it in with the index attached.
            continue
        if (table, index_name) in existing:
            continue
        op.create_index(index_name, table, columns)


def downgrade() -> None:
    existing = set(_all_indexes())

    for index_name, table, _columns in _planned():
        if (table, index_name) not in existing:
            continue
        op.drop_index(index_name, table_name=table)
