"""Widen every free-text column from TEXT to NVARCHAR(MAX)

Revision ID: 0012_unicode_text_columns
Revises: 0011_audit_log_context
Create Date: 2026-09-04

SQLAlchemy's generic `Text` compiles to SQL Server `TEXT`, which is both
deprecated (Microsoft, 2005) and — under this deployment's
SQL_Latin1_General_CP1_CI_AS collation — *non-Unicode*. Any character
outside that codepage was silently replaced with "?" on write. Not an
error, not a warning: the data was simply destroyed as it was stored.

That mattered here more than it would in most schemas. The voice agent
advertises English, Hindi and Telugu, so `transcript_messages.text` —
which holds what a patient actually said — was being mangled on every
non-English call, and a clinic that wrote its greeting in Devanagari got
question marks read back. 21 columns across 13 tables were affected.

This migration only widens the type. It cannot recover characters that
were already replaced: "?" is what is stored, and the original bytes are
gone. Anything written from here on survives.

SQLite (used by the test suite) has no fixed-width text types and stores
UTF-8 throughout, so it was never affected and needs no change — the
alters are skipped on any non-mssql dialect.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mssql

revision: str = "0012_unicode_text_columns"
down_revision: Union[str, None] = "0011_audit_log_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, nullable). Nullability has to be restated: an ALTER
# COLUMN that omits it on SQL Server silently makes the column nullable.
_COLUMNS = [
    ("plans", "description", True),
    ("agent_settings", "greeting_script", True),
    ("clinic_info", "timings_text", True),
    ("clinic_info", "insurance_info", True),
    ("clinic_info", "general_info", True),
    ("ehr_integrations", "detail", True),
    ("ehr_integrations", "note", True),
    ("ehr_integrations", "api_credentials_json", True),
    ("faqs", "answer", False),
    ("notifications", "message", True),
    ("notifications", "data_json", True),
    ("services", "description", True),
    ("interactions", "detail", True),
    ("super_admin_audit_log", "detail", True),
    ("support_tickets", "message", False),
    ("call_logs", "ai_summary", True),
    ("call_logs", "actions_taken", True),
    ("call_logs", "extracted_json", True),
    ("call_logs", "callback_note", True),
    ("call_evaluations", "policy_violations_json", True),
    ("transcript_messages", "text", False),
]


def _alter(to_type, existing_type) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mssql":
        return

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, column, nullable in _COLUMNS:
        if table not in tables:
            continue
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            continue
        op.alter_column(
            table,
            column,
            existing_type=existing_type,
            type_=to_type,
            existing_nullable=nullable,
            nullable=nullable,
        )


def upgrade() -> None:
    _alter(mssql.NVARCHAR(None), mssql.TEXT())


def downgrade() -> None:
    # Lossy in the other direction: anything outside the collation's
    # codepage becomes "?" on the way back down.
    _alter(mssql.TEXT(), mssql.NVARCHAR(None))
