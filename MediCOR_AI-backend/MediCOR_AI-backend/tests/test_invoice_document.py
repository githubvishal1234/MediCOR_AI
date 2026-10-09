"""
Invoice documents (GAP-B2).

Invoices could be listed and not opened. A "Download All" button had sat
on the Billing page with no handler and no endpoint, and was removed
rather than wired up, because there was nothing to wire it to.

**Read this before extending it.** Nothing in this application issues an
invoice. There is no billing run and no payment provider -- `POST
/billing/upgrade` returns 501 by design -- and the only code that has
ever written an `invoices` row is `app/seed.py`, for the demo clinic.

That shapes what this endpoint may do. It reproduces four stored fields
under the clinic's own name and says on its face where they came from.
It does not compute a charge, does not assert a payment was taken, and
does not present itself as a tax document. A document that *did* would
be a fabricated financial record for a transaction that never happened
-- worse than the missing button it replaces. The provenance note is
therefore load-bearing, and
`test_it_does_not_claim_to_be_a_receipt_or_a_tax_document` is what keeps
it there until real billing exists.

    pytest tests/test_invoice_document.py -v
"""

import os
import sys
from datetime import datetime

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models, security  # noqa: E402
from app.config import settings  # noqa: E402
from app.routers.billing import _CURRENCY_SYMBOLS  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.routers import billing  # noqa: E402


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()

    org = models.Organization(name="Northside Clinic", suspended=False)
    other = models.Organization(name="Southside Clinic", suspended=False)
    db.add_all([org, other])
    db.flush()

    user = models.User(
        org_id=org.id, email="nurse@northside.test", full_name="Nurse Rao",
        hashed_password=security.hash_password("x"), role="medical_staff",
        is_active=True,
    )
    db.add(user)
    db.flush()

    invoice = models.Invoice(
        org_id=org.id, invoice_number="INV-2026-0901",
        issued_at=datetime(2026, 9, 1), amount_cents=49900, status="Paid",
    )
    theirs = models.Invoice(
        org_id=other.id, invoice_number="INV-2026-0902",
        issued_at=datetime(2026, 9, 1), amount_cents=1000, status="Paid",
    )
    db.add_all([invoice, theirs])
    db.commit()

    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_db] = lambda: db

    token = security.create_access_token({"sub": user.id})
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {token}"

    yield {
        "client": client, "db": db, "org": org,
        "invoice": invoice, "theirs": theirs, "user": user,
    }
    db.close()


# Spelled out rather than written as escape sequences: this file gets
# edited by scripts, and an escape that survives one round trip but
# not the next leaves a test that silently stops testing anything.
CR, LF = chr(13), chr(10)


def _doc(env, invoice=None):
    inv = invoice or env["invoice"]
    return env["client"].get(f"/billing/invoices/{inv.id}/document")


# ================================================== it renders the row

def test_the_document_carries_the_stored_figures(env):
    res = _doc(env)
    assert res.status_code == 200
    body = res.text

    assert "INV-2026-0901" in body
    # The figure, not the symbol. The document renders the deployment's
    # configured currency (BILLING_CURRENCY) rather than a hardcoded "$",
    # which was wrong the moment the platform started billing in rupees.
    assert "499.00" in body
    assert _CURRENCY_SYMBOLS.get(settings.billing_currency.upper(),
                                 settings.billing_currency.upper()) in body
    assert "Northside Clinic" in body
    assert "Sep 1, 2026" in body
    assert "Paid" in body


def test_it_is_a_self_contained_page(env):
    """
    One file with no external references, so it prints and archives the
    same wherever it is opened -- which is also why it needs no PDF
    library.
    """
    body = _doc(env).text
    assert body.startswith("<!doctype html>")
    assert "<style>" in body
    assert "<link" not in body
    assert "<script" not in body


def test_it_opens_in_the_browser_rather_than_downloading(env):
    """
    The browser's own Print-to-PDF is what makes this a file. A forced
    download would put an .html in Downloads that somebody then has to
    find and open before they can print it.
    """
    res = _doc(env)
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["content-disposition"].startswith("inline")


# ===================================== it does not overstate what it is

def test_it_does_not_claim_to_be_a_receipt_or_a_tax_document(env):
    """
    The load-bearing test in this file.

    No code in this application issues an invoice or takes a payment.
    A document asserting either would be a fabricated financial record.
    When real billing lands (HV-5) the row becomes authoritative and this
    note should be removed *then*, deliberately -- not now, and not by
    accident.
    """
    # Whitespace-normalised: the note is wrapped for legibility in the
    # source, and a test that breaks on re-wrapping would get deleted
    # rather than fixed.
    body = " ".join(_doc(env).text.lower().split())

    assert "not processed by this application" in body, (
        "the provenance note is gone; this document now reads as though "
        "the application charged the clinic, which it has never done"
    )
    assert "rather than a payment receipt or a tax document" in body

    for overclaim in ("payment received", "thank you for your payment",
                      "tax invoice", "amount paid", "paid in full"):
        assert overclaim not in body, f"the document asserts {overclaim!r}"


def test_no_figure_is_invented(env):
    """
    Every number on the page comes off the row. Nothing computes tax,
    discounts or a running balance, because nothing in this system knows
    any of those.
    """
    body = _doc(env).text
    # Currency-agnostic on purpose -- see the note above.
    assert body.count("499.00") == 2  # the line item and the total
    for invented in ("Tax", "VAT", "GST", "Discount", "Balance due", "Subtotal"):
        assert invented not in body


# ============================================================ boundary

def test_another_clinics_invoice_is_not_reachable(env):
    res = _doc(env, env["theirs"])
    assert res.status_code == 404


def test_an_unknown_invoice_is_a_404(env):
    res = env["client"].get("/billing/invoices/does-not-exist/document")
    assert res.status_code == 404


def test_markup_in_a_stored_field_cannot_reach_the_page(env):
    """
    `invoice_number` and `status` are unbounded free text a super admin
    can set, and an invoice is a document a clinic forwards to their
    accountant -- so it renders in somebody else's browser.
    """
    env["invoice"].invoice_number = "<script>alert(1)</script>"
    env["invoice"].status = "<img src=x onerror=alert(2)>"
    env["db"].commit()

    body = _doc(env).text
    # The assertion to write is "no tag was created", not "the substring
    # is absent": `&lt;img src=x onerror=alert(2)&gt;` still contains the
    # literal text and is completely inert, so testing for the text would
    # fail a correct implementation.
    assert "<img" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;img src=x onerror=alert(2)&gt;" in body


def test_a_newline_in_a_stored_field_cannot_forge_a_header(env):
    """
    HTML escaping is not header escaping. It makes "<" safe and leaves a
    newline entirely intact -- and a newline in a response header is
    header injection -- so the filename is built from a whitelist rather
    than by reusing the escaped value.
    """
    env["invoice"].invoice_number = "INV-1" + CR + LF + "X-Injected: yes"
    env["db"].commit()

    res = _doc(env)
    assert res.status_code == 200
    assert "x-injected" not in {k.lower() for k in res.headers}
    disposition = res.headers["content-disposition"]
    assert CR not in disposition and LF not in disposition
    # The hyphen survives -- it is in the whitelist, and it belongs in an
    # invoice number. The CR and LF are what had to go.
    assert disposition == 'inline; filename="invoice-INV-1X-Injectedyes.html"'


# =============================================================== audit

def test_opening_one_is_recorded(env):
    _doc(env)
    entries = env["db"].query(models.AuditLogEntry).all()
    assert [e.action for e in entries] == ["invoice.downloaded"]
    assert entries[0].target_id == env["invoice"].id


def test_probing_an_id_records_nothing(env):
    """
    Record after the 404. A failed lookup that wrote an entry would let
    anyone manufacture "document accessed" rows by guessing ids.
    """
    env["client"].get("/billing/invoices/nope/document")
    _doc(env, env["theirs"])
    assert env["db"].query(models.AuditLogEntry).count() == 0


def test_the_action_is_in_the_closed_vocabulary():
    """
    The Security page filters on `audit_service.ACTIONS`; a code outside
    it is invisible there.
    """
    from app import audit_service

    assert "invoice.downloaded" in audit_service.ACTIONS


# ================================================= what still is not built

def test_the_set_of_invoice_writers_is_known():
    """
    This test used to assert that NOTHING issued an invoice, and named
    itself as the signal to revisit the document when that changed. HV-5
    changed it: `billing_service.fulfil_payment` writes an invoice when a
    Razorpay payment is captured, and the document now reads as a receipt
    for those rows (see the paid-path tests below).

    It stays as a tripwire, with a wider allow-list. A THIRD writer
    appearing is the interesting event now -- most likely a billing run,
    which would raise the same question this test asked the first time:
    does the wording still describe every row it can be shown for?
    """
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    writers = sorted(
        path.name for path in app_dir.rglob("*.py")
        if "models.Invoice(" in path.read_text(encoding="utf-8")
    )
    assert writers == ["billing_service.py", "seed.py"], (
        f"the set of invoice writers changed ({writers}); the document has "
        "two notes -- one for a captured payment and one for a row nobody "
        "paid -- and a new writer needs to say which it produces"
    )


def test_a_paid_invoice_reads_as_a_receipt(env):
    """
    The other half of the rule. A row backed by a captured payment may
    say so: money actually moved, through Razorpay, and refusing to
    acknowledge it would be its own kind of inaccuracy.
    """
    db = env["db"]
    payment = models.Payment(
        org_id=env["org"].id, plan_name="Growth", provider="razorpay",
        provider_order_id="order_paidtest", provider_payment_id="pay_abc123",
        amount_cents=29900, currency="INR", status="captured",
        captured_at=datetime.utcnow(),
    )
    db.add(payment)
    db.commit()
    inv = env["invoice"]
    inv.payment_id = payment.id
    db.commit()

    body = " ".join(_doc(env).text.split())
    assert "Receipt for a payment of" in body
    assert "pay_abc123" in body, "the provider reference must be quotable"
    assert "not processed by this application" not in body, (
        "this row WAS processed by this application"
    )


def test_even_a_paid_invoice_is_not_a_tax_document(env):
    """
    No tax is calculated, withheld or reported anywhere in this codebase.
    A receipt may say a payment happened; calling itself a tax invoice
    would be a fabricated financial record, which is the rule the
    unpaid-path note has always enforced.
    """
    db = env["db"]
    payment = models.Payment(
        org_id=env["org"].id, plan_name="Growth", provider="razorpay",
        provider_order_id="order_taxtest", provider_payment_id="pay_tax",
        amount_cents=29900, currency="INR", status="captured",
        captured_at=datetime.utcnow(),
    )
    db.add(payment)
    db.commit()
    inv = env["invoice"]
    inv.payment_id = payment.id
    db.commit()

    body = " ".join(_doc(env).text.lower().split())
    assert "not a tax document" in body
    for overclaim in ("tax invoice", "gst", "vat registered"):
        assert overclaim not in body, f"the receipt asserts {overclaim!r}"


def test_an_uncaptured_payment_does_not_make_it_a_receipt(env):
    """
    A created-but-unpaid order must not upgrade the wording. The check is
    on the payment STATUS, not merely on the link existing.
    """
    db = env["db"]
    payment = models.Payment(
        org_id=env["org"].id, plan_name="Growth", provider="razorpay",
        provider_order_id="order_pending", amount_cents=29900,
        currency="INR", status="created",
    )
    db.add(payment)
    db.commit()
    inv = env["invoice"]
    inv.payment_id = payment.id
    db.commit()

    body = " ".join(_doc(env).text.split())
    assert "Receipt for a payment" not in body
    assert "not processed by this application" in body
