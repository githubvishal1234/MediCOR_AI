"""somewhere for a public contact enquiry to land

Revision ID: 0035_contact_enquiries
Revises: 0034_drop_insurance
Create Date: public pages audit

  contact_enquiries  -- a visitor asking to be contacted

WHY A TABLE AND NOT A mailto:

The footer offered "Contact Sales" as an anchor to a section that did not
exist, so it scrolled nowhere. A `mailto:` would have been the cheap fix
and it fails in the ways mail always fails: it depends on the visitor
having a mail client configured, it leaves the address to be harvested,
and nothing on our side records that somebody asked. An enquiry that
arrives and is lost is worse than a link that visibly does nothing,
because nobody finds out.

WHY THIS IS THE ONLY TABLE WITH NO org_id

Every other table in this schema carries one, and the tenancy rules in
CLAUDE.md say every query must filter on it. This row has no tenant to
filter by: whoever filled the form is not a customer, which is the whole
reason they are writing. It is platform correspondence and belongs beside
`support_tickets` in the super-admin console -- not inside any clinic.

Attaching an org by matching `clinic_name` against `organizations` would
be a guess presented as a fact, the same call as refusing to fuzzy-match
a refill medication name or two spellings of a patient's name.

BOUNDED LENGTHS, AND WHICH ARE INDEXED

`status` and `created_at` are indexed because the console reads this as
an inbox: new ones first, unhandled ones filtered. Everything indexable
is bounded -- a bare `String` compiles to VARCHAR(MAX), which SQL Server
cannot index, and `tests/test_indexable_columns_are_bounded.py` fails the
build for exactly that. `message` is UNICODE_TEXT (NVARCHAR(MAX)) because
a human wrote it and this schema has already lost one set of transcripts
to a non-Unicode column.

`source_ip` exists for abuse triage only. The endpoint is public and rate
limited per IP; without the address recorded, a flood cannot be told from
a busy week.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "0035_contact_enquiries"
down_revision = "0034_drop_insurance"
branch_labels = None
depends_on = None


def upgrade():
    # The idiom the other migrations use: NVARCHAR(MAX) on SQL Server,
    # ordinary UnicodeText on the SQLite the tests run against.
    unicode_text = sa.UnicodeText().with_variant(mssql.NVARCHAR(None), "mssql")

    op.create_table(
        "contact_enquiries",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("clinic_name", sa.String(length=200), nullable=True),
        sa.Column("message", unicode_text, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column(
            "handled_by",
            sa.String(length=64),
            sa.ForeignKey("super_admins.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_contact_enquiries_status", "contact_enquiries", ["status"]
    )
    op.create_index(
        "ix_contact_enquiries_created_at", "contact_enquiries", ["created_at"]
    )


def downgrade():
    # Drops the enquiries with the table. There is no other copy: nothing
    # is emailed onward, by design, so a downgrade that has to run should
    # export the rows first.
    op.drop_index("ix_contact_enquiries_created_at", table_name="contact_enquiries")
    op.drop_index("ix_contact_enquiries_status", table_name="contact_enquiries")
    op.drop_table("contact_enquiries")
