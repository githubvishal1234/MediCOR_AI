"""
Every free-text column must be Unicode on SQL Server.

This is one of the failures the rest of the suite structurally cannot
catch. Tests run on in-memory SQLite, which stores UTF-8 everywhere and
has no fixed-width text types — so a column declared `Text` round-trips
Devanagari perfectly here and silently replaces it with "?" in
production, where the collation is SQL_Latin1_General_CP1_CI_AS.

`transcript_messages.text` holds what a patient actually said, and the
voice agent advertises English, Hindi and Telugu. So these assertions are
made against the *compiled mssql DDL* rather than against behaviour.

    pytest tests/test_unicode_text_columns.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import Text
from sqlalchemy.dialects import mssql

from app import models

# Types Microsoft deprecated in 2005. TEXT and NTEXT are additionally the
# non-Unicode / legacy pair that this whole file exists to keep out.
DEPRECATED = ("TEXT", "NTEXT", "IMAGE")


def _mssql_ddl(column):
    return column.type.compile(mssql.dialect()).upper()


def _all_columns():
    for table in models.Base.metadata.sorted_tables:
        for column in table.columns:
            yield table.name, column


def test_no_column_is_declared_plain_text():
    """
    `Column(Text)` compiles to non-Unicode TEXT on SQL Server. Use the
    UNICODE_TEXT variant defined at the top of models.py instead.
    """
    offenders = [
        f"{table}.{column.name}"
        for table, column in _all_columns()
        if type(column.type) is Text
    ]
    assert offenders == [], (
        "these compile to non-Unicode TEXT on SQL Server and will store "
        f"'?' for any character outside the collation codepage: {offenders}"
    )


def test_no_column_compiles_to_a_deprecated_lob_type():
    """Catches generic UnicodeText (-> NTEXT) and LargeBinary (-> IMAGE) too."""
    offenders = []
    for table, column in _all_columns():
        ddl = _mssql_ddl(column)
        if ddl in DEPRECATED:
            offenders.append(f"{table}.{column.name} -> {ddl}")
    assert offenders == [], f"deprecated SQL Server LOB types: {offenders}"


@pytest.mark.parametrize(
    "table_name, column_name",
    [
        ("transcript_messages", "text"),        # what the patient said
        ("agent_settings", "greeting_script"),  # what the AI says first
        ("clinic_info", "general_info"),
        ("faqs", "answer"),
        ("call_logs", "ai_summary"),
        ("kb_chunks", "text"),
    ],
)
def test_patient_facing_text_is_nvarchar_max(table_name, column_name):
    table = models.Base.metadata.tables[table_name]
    assert _mssql_ddl(table.c[column_name]) == "NVARCHAR(MAX)"


def test_the_unicode_variant_still_maps_to_nvarchar_max():
    """Guards the helper itself — everything above depends on it."""
    assert models.UNICODE_TEXT.compile(mssql.dialect()).upper() == "NVARCHAR(MAX)"
    assert models.BINARY_BLOB.compile(mssql.dialect()).upper() == "VARBINARY(MAX)"
