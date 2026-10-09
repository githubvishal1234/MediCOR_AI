"""Finish the job 0012 started: convert the remaining non-Unicode text columns

Revision ID: 0021_unicode_text_repair
Revises: 0020_appointment_link_confirm
Create Date: PF follow-up

Migration `0012_unicode_text_columns` converted 21 free-text columns to
`NVARCHAR(MAX)` so that Hindi and Telugu survive being written. On at
least one deployment it never ran, and the columns are still
`VARCHAR(MAX)` -- verified by inspecting `INFORMATION_SCHEMA.COLUMNS`
directly rather than trusting the migration history.

**How a database can end up here.** This application ships with
`AUTO_CREATE_SCHEMA` on, which runs `create_all()` plus
`database.sync_missing_columns()` at startup. That combination creates
tables from whatever the model said at the time and thereafter only ever
*adds* columns -- it never alters an existing one's type. So a database
first created before 0012 landed, and then kept current by auto-sync
alone, has every pre-0012 text column frozen at its original type while
every column added since is correct. `alembic_version` did not exist on
that database at all, so nothing recorded that 0012 had been skipped.

**Why this is not simply 0012 re-run.** 0012 declares
`existing_type=mssql.TEXT()`, because that is what SQLAlchemy's generic
`Text` was expected to compile to. What is actually on disk is
`VARCHAR(MAX)`. The emitted DDL does not depend on `existing_type`, so
0012 would very likely have worked -- but re-running a migration the
history has already moved past is worse than writing an honest new one
that says what it found.

This migration therefore:

  * decides per column, from live reflection, whether anything needs
    doing -- so it is safe on a database where 0012 *did* run, and safe
    to run twice;
  * does not assert what the source type is, because the whole reason
    this exists is that the source type was not what was assumed;
  * restates nullability explicitly. An `ALTER COLUMN` that omits it on
    SQL Server silently makes the column nullable, which would quietly
    drop three NOT NULL constraints.

**What it cannot do.** It does not repair existing data. Characters
outside the `SQL_Latin1_General_CP1_CI_AS` codepage were replaced with
"?" at write time; the original bytes are gone and no conversion brings
them back. Every transcript, summary and greeting written from here on
survives -- the ones already mangled stay mangled.

None of these 21 columns carries an index or constraint (checked), so
none of the alters can be blocked by one. If that ever changes, SQL
Server will refuse the alter loudly rather than silently, which is the
failure mode to want.

SQLite -- used by the test suite -- has no fixed-width text types and
stores UTF-8 throughout, so it was never affected and is skipped.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision: str = "0021_unicode_text_repair"
down_revision: Union[str, None] = "0020_appointment_link_confirm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, nullable). The same 21 columns 0012 named, with
# nullability re-verified against the live schema rather than copied.
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

# Types that already store Unicode. NTEXT is deprecated but does hold it,
# so a column sitting on one is left alone rather than churned.
_ALREADY_UNICODE = {"NVARCHAR", "NTEXT", "NCHAR"}


def _current_type(inspector, table: str, column: str):
    for c in inspector.get_columns(table):
        if c["name"] == column:
            return c["type"]
    return None


def _convert(to_type, *, want_unicode: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mssql":
        return

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table, column, nullable in _COLUMNS:
        if table not in tables:
            continue
        current = _current_type(inspector, table, column)
        if current is None:
            continue

        is_unicode = type(current).__name__.upper() in _ALREADY_UNICODE
        if is_unicode == want_unicode:
            # Already where this migration wants it. Skipping rather than
            # re-issuing the alter keeps the run idempotent, and means a
            # database on which 0012 *did* run is untouched.
            continue

        op.alter_column(
            table,
            column,
            existing_type=current,
            type_=to_type,
            existing_nullable=nullable,
            nullable=nullable,
        )


def upgrade() -> None:
    _convert(mssql.NVARCHAR(None), want_unicode=True)


def downgrade() -> None:
    # Lossy, and worth stating plainly: going back down replaces every
    # non-Latin character with "?" as it is rewritten. Provided for
    # completeness, not because anyone should run it.
    _convert(mssql.VARCHAR(None), want_unicode=False)
