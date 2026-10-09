"""patient contact consent

Revision ID: 0019_patient_contact_consent
Revises: 0018_webhook_delivery
Create Date: PF-05

Whether a patient has agreed to be messaged, and a way for them to stop.

Nothing recorded this before. That was harmless only because no transport
was ever configured, so `notification_service` composed four kinds of
message and sent none of them. It stops being harmless the instant mail
or WhatsApp credentials are set -- which is exactly why this lands *with*
messaging rather than after it. Retrofitting consent once messages are
already going out means retrofitting it after the first complaint.

Three columns, all nullable:

  contact_consent         "granted" | "declined" | NULL   String(16)
  contact_consent_at      when the answer was recorded    DateTime
  contact_consent_source  "voice" | "staff" | "reply"     String(16)

**NULL is a third state, not a missing value.** It means nobody has ever
asked, and it is what every row created before this migration holds. The
distinction carries real weight in both directions: treating NULL as a
refusal would silently mute a clinic's entire existing roster the day
messaging is switched on, and treating it as consent would message people
who never agreed to anything. So the reading is deliberately not encoded
in the schema -- `notification_service.may_contact` resolves it from
`contact_unasked_is_consent`, which defaults to the cautious answer.

`contact_consent_source` exists because "did we ever ask, and how" is the
question a complaint actually turns on, and the audit log cannot answer
it: `audit_service.changed_fields` records field *names*, never values,
so an audit entry can only say that consent changed.

Bounded lengths for the usual reason on this deployment -- a bare
`Column(String)` is VARCHAR(MAX) on SQL Server, which cannot be indexed,
and "which patients may we contact" is exactly the filter a clinic would
eventually want. See tests/test_indexable_columns_are_bounded.py.

Two things deliberately NOT done here:

  * No backfill. Writing "granted" into existing rows would manufacture a
    consent nobody gave, which is the whole failure this column exists to
    prevent. NULL is the honest value and the configuration flag decides
    what to do about it.

  * Not added to `PatientListOut`. That lean shape is what the voice
    agent's patient lookup hands straight into a live LLM prompt; whether
    a patient may be messaged is not something the model needs, and every
    field there costs tokens on every call.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_patient_contact_consent"
down_revision: Union[str, None] = "0018_webhook_delivery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("patients", sa.Column("contact_consent", sa.String(length=16), nullable=True))
    op.add_column("patients", sa.Column("contact_consent_at", sa.DateTime(), nullable=True))
    op.add_column(
        "patients", sa.Column("contact_consent_source", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("patients", "contact_consent_source")
    op.drop_column("patients", "contact_consent_at")
    op.drop_column("patients", "contact_consent")
