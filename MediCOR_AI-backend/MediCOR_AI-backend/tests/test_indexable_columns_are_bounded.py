"""
Anything indexed, unique or a primary key must have a bounded length.

`Column(String)` compiles to VARCHAR(MAX) on SQL Server, which cannot
carry an index, a UNIQUE constraint or a primary key — the CREATE TABLE
is rejected outright. SQLite has no such limit, so the rest of the suite
passes happily and the failure only appears on the deployment.

That is not hypothetical here: `tests/test_phase5_plan_column.py` exists
because a UNIQUE on an unbounded String passed the suite and then failed
at table creation on SQL Server.

The 101 unbounded String columns still in models.py are deliberately NOT
failed by this file. None of them is indexed, unique or a key, so none is
currently broken, and retrofitting a length onto a column holding real
data risks truncating it for no present benefit. What this guards is the
next one: add an index or a unique constraint to an unbounded column and
the build stops here instead of on the server.

    pytest tests/test_indexable_columns_are_bounded.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import String  # noqa: E402
from sqlalchemy.dialects import mssql  # noqa: E402

from app import models  # noqa: E402


def _is_unbounded_string(column) -> bool:
    """True for String/Unicode with no length — i.e. VARCHAR(MAX) on mssql."""
    if not isinstance(column.type, String):
        return False
    if getattr(column.type, "length", None):
        return False
    # UNICODE_TEXT / BINARY_BLOB are intentionally unbounded LOBs and are
    # never index targets; they compile to NVARCHAR(MAX)/VARBINARY(MAX)
    # and are covered by tests/test_unicode_text_columns.py instead.
    return "MAX" in column.type.compile(mssql.dialect()).upper()


def _indexed_column_names(table) -> set[str]:
    """Every column named by an Index or a UniqueConstraint on this table."""
    names: set[str] = set()
    for index in table.indexes:
        names.update(c.name for c in index.columns)
    for constraint in table.constraints:
        if constraint.__class__.__name__ == "UniqueConstraint":
            names.update(c.name for c in constraint.columns)
    return names


def test_no_indexed_or_unique_column_is_unbounded():
    offenders = []
    for table in models.Base.metadata.sorted_tables:
        indexed = _indexed_column_names(table)
        for column in table.columns:
            if not _is_unbounded_string(column):
                continue
            reasons = []
            if column.primary_key:
                reasons.append("primary key")
            if column.index:
                reasons.append("index=True")
            if column.unique:
                reasons.append("unique=True")
            if column.name in indexed:
                reasons.append("named by an Index/UniqueConstraint")
            if reasons:
                offenders.append(f"{table.name}.{column.name} ({', '.join(reasons)})")

    assert offenders == [], (
        "these compile to VARCHAR(MAX) on SQL Server, which cannot be "
        "indexed, made UNIQUE or used as a key — give them an explicit "
        f"length, e.g. String(120): {offenders}"
    )


def test_foreign_keys_are_bounded_too():
    """
    A FK has to match the referenced key's type, and every primary key
    here is a bounded String(64). An unbounded FK column would fail the
    constraint creation rather than the column creation, which is a
    noisier place to find out.
    """
    offenders = [
        f"{table.name}.{column.name}"
        for table in models.Base.metadata.sorted_tables
        for column in table.columns
        if column.foreign_keys and _is_unbounded_string(column)
    ]
    assert offenders == [], f"unbounded foreign-key columns: {offenders}"


def test_the_guard_would_actually_catch_a_regression():
    """
    A guard that cannot fail is not a guard. Builds a throwaway table with
    an indexed unbounded String and checks the detector flags it.
    """
    from sqlalchemy import Column, MetaData, Table

    md = MetaData()
    t = Table(
        "throwaway",
        md,
        Column("id", String(64), primary_key=True),
        Column("bad", String, index=True),
        Column("fine", String(120), index=True),
        Column("also_fine", String),  # unbounded but not indexed
    )

    flagged = {
        c.name for c in t.columns if _is_unbounded_string(c) and (c.index or c.primary_key)
    }
    assert flagged == {"bad"}
