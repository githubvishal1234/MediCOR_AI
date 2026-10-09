"""what the patient thought

Revision ID: 0025_patient_feedback
Revises: 0024_callback_message
Create Date: PF-10

  patient_feedback              new table
  call_logs.feedback_requested_at   DateTime, nullable

The product has always been thorough about its own opinion of itself.
`call_evaluations` scores every call with a language model as judge,
flags hallucinations and policy violations, and queues them for human
review. All of that is the system marking its own homework. Nothing
anywhere asked the patient.

This closes an asymmetry rather than filling a feature gap, and the
interesting number it produces is not the average -- it is the gap
between what the judge thought of a call and what the person on the other
end of it thought.

**Its own table, not columns on `call_logs`.** Feedback is a fact
contributed by a different party at a different time; a call record is
not amended by it. Same reasoning that gave repeat-prescription requests
their own table rather than a status on `prescriptions`.

`rating` is NOT NULL and `comment` is free text that is stored and never
parsed -- a person reads it. `call_id` and `patient_id` are nullable so
feedback outlives the call it describes.

`feedback_requested_at` on `call_logs` is the "we asked" marker, written
on dispatch for the same reason `reminder_sent_at` is: the transports
swallow their own failures, so arrival is not knowable, and asking twice
is worse than not knowing.

Indexed on (org_id, created_at), matching `call_evaluations` -- the query
this table exists to serve is "how did our patients rate us lately".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_patient_feedback"
down_revision: Union[str, None] = "0024_callback_message"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_feedback",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("call_id", sa.String(length=64), nullable=True),
        sa.Column("patient_id", sa.String(length=64), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.UnicodeText(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["call_id"], ["call_logs.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_patient_feedback_org_created", "patient_feedback", ["org_id", "created_at"]
    )
    op.create_index("ix_patient_feedback_call_id", "patient_feedback", ["call_id"])
    op.create_index("ix_patient_feedback_patient_id", "patient_feedback", ["patient_id"])

    op.add_column(
        "call_logs", sa.Column("feedback_requested_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("call_logs", "feedback_requested_at")
    op.drop_index("ix_patient_feedback_patient_id", table_name="patient_feedback")
    op.drop_index("ix_patient_feedback_call_id", table_name="patient_feedback")
    op.drop_index("ix_patient_feedback_org_created", table_name="patient_feedback")
    op.drop_table("patient_feedback")
