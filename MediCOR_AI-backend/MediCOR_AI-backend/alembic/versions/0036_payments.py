"""record an attempt to pay for a plan (HV-5, Razorpay)

Revision ID: 0036_payments
Revises: 0035_contact_enquiries
Create Date: HV-5 payments

  payments  -- one order, and whether it was captured

WHAT THIS REPLACES

Nothing, which is the point. Before this there was no payment path at
all: `POST /billing/upgrade` used to set `plan = "Enterprise"` and commit
with no money involved, so any clinic admin could grant themselves the
top plan for free. That was closed to a deliberate 501, and the Billing
page has since told clinics to contact us. Meanwhile the page showed
"Visa ending in 4242" and a next billing date, both seeded demo values,
so it read as though a card were on file and a charge were scheduled.

THE UNIQUE CONSTRAINT IS THE IDEMPOTENCY

`provider_order_id` is UNIQUE and it is the whole safety mechanism.
Razorpay delivers a webhook at least once, *and* the browser posts the
checkout result back, so a successful payment reaches this application at
least twice by design. Both paths look the order up by this id, and
fulfilment is written once. Without the constraint a retried webhook
means two invoices for one payment.

Bounded lengths for the same reason as everywhere else: a bare `String`
compiles to VARCHAR(MAX), and SQL Server cannot put a UNIQUE constraint
or an index on that. `tests/test_indexable_columns_are_bounded.py` fails
the build for it.

WHY plan_id IS NULLABLE

The money moved. That record must not disappear because a super admin
later tidied a plan out of the catalogue, so the foreign key is nullable
and `plan_name` keeps a copy of what was actually bought.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_payments"
down_revision = "0035_contact_enquiries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=64),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.String(length=64), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("plan_name", sa.String(length=120), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_order_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("created_by", sa.String(length=64), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("fulfilled_via", sa.String(length=20), nullable=True),
    )
    op.create_index("ix_payments_org_id", "payments", ["org_id"])
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index("ix_payments_created_at", "payments", ["created_at"])

    # Which payment produced an invoice, when one did.
    #
    # Nullable, and NOT backfilled. Every existing row came from the seed
    # script and describes a charge this application never took -- which
    # is exactly why the invoice document states that charges are not
    # processed here. A row with a payment is a different kind of thing
    # and may honestly read as a receipt, so this column is what lets one
    # endpoint serve both without either lying.
    op.add_column(
        "invoices",
        sa.Column("payment_id", sa.String(length=64), sa.ForeignKey("payments.id"), nullable=True),
    )


def downgrade():
    # Drops the payment records with the table. They are the only
    # evidence in this database that money changed hands, so export them
    # before running this on anything real.
    op.drop_column("invoices", "payment_id")
    op.drop_index("ix_payments_created_at", table_name="payments")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_org_id", table_name="payments")
    op.drop_table("payments")
