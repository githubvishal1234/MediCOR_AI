"""kb_chunks: embedded document passages for RAG

Revision ID: 0009_kb_chunks
Revises: 0008_llm_call_analysis
Create Date: AI-3

There was no retrieval before this. An uploaded document was written to
disk and immediately marked "Indexed" without the file ever being opened,
and /knowledge-base/search matched on filename only — so every document a
clinic uploaded was dead weight.

`embedding` holds raw float32 bytes (384 dims -> 1536 bytes). This
deployment is SQL Server 2022 Express, which has no native VECTOR type
(that arrived in 2025), so similarity is computed in numpy over one org's
chunks rather than in the database. At clinic scale that is faster than a
round trip and avoids operating a second datastore holding PHI-adjacent
text.

Note the column types: NVARCHAR(MAX) and VARBINARY(MAX), not the NTEXT
and IMAGE that SQLAlchemy's generic UnicodeText/LargeBinary compile to on
mssql. Those are deprecated (Microsoft, 2005) and NTEXT/TEXT are also
non-Unicode under this server's SQL_Latin1_General_CP1 collation — see
the note in CLAUDE.md. A clinic handbook has to survive holding a rupee
sign or a name in Devanagari.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mssql

revision: str = "0009_kb_chunks"
down_revision: Union[str, None] = "0008_llm_call_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "kb_chunks"
_INDEX = "ix_kb_chunks_org_document"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "org_id", sa.String(length=64),
                sa.ForeignKey("organizations.id"), nullable=False,
            ),
            sa.Column(
                "document_id", sa.String(length=64),
                sa.ForeignKey("kb_documents.id"), nullable=True,
            ),
            sa.Column("chunk_index", sa.Integer(), nullable=True),
            sa.Column(
                "text",
                sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql"),
                nullable=False,
            ),
            sa.Column(
                "embedding",
                sa.LargeBinary().with_variant(mssql.VARBINARY(None), "mssql"),
                nullable=True,
            ),
            sa.Column("char_count", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    inspector = sa.inspect(op.get_bind())
    if _INDEX not in {i["name"] for i in inspector.get_indexes(_TABLE) if i.get("name")}:
        # Retrieval always scans one org; document_id lets a re-index
        # delete just that file's chunks.
        op.create_index(_INDEX, _TABLE, ["org_id", "document_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE in set(inspector.get_table_names()):
        if _INDEX in {i["name"] for i in inspector.get_indexes(_TABLE) if i.get("name")}:
            op.drop_index(_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
