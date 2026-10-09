"""
Pins three fixes from the pre-submission security review.

- Webhooks never reach a private/internal address (SSRF).
- SMS replies escape their text (TwiML/XML injection).
- CSV export cells cannot run as spreadsheet formulas.
"""
from types import SimpleNamespace

import httpx
import pytest

from app import webhook_service
from app.routers.calls import _csv_safe
from app.routers.inbound_messages import _twiml


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/hook",
    "http://localhost/hook",
    "http://10.0.0.5/hook",
    "http://192.168.29.53:1433/",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/hook",
    "ftp://8.8.8.8/hook",
    "not a url",
])
def test_internal_or_malformed_webhook_targets_are_refused(url):
    assert webhook_service.blocked_target(url)


def test_a_public_address_is_allowed():
    assert webhook_service.blocked_target("https://8.8.8.8/hook") is None


def test_a_refused_target_is_never_contacted(monkeypatch):
    def must_not_send(*a, **k):
        raise AssertionError("httpx.post called for a private address")
    monkeypatch.setattr(httpx, "post", must_not_send)
    hook = SimpleNamespace(endpoint_url="http://192.168.29.53:1433/", secret="s")
    delivered, status = webhook_service.post_body(hook, "call.completed", "d1", b"{}")
    assert delivered is False and status.startswith("refused")


def test_sms_reply_text_is_escaped():
    body = _twiml('Tue & Wed <Message to="+100">x</Message>').body.decode()
    assert "&amp;" in body and "&lt;Message" in body
    assert body.count("<Message>") == 1


def test_csv_cells_cannot_be_formulas():
    assert _csv_safe('=HYPERLINK("http://x","y")').startswith("'")
    assert _csv_safe("@SUM(A1)").startswith("'")
    assert _csv_safe("-2+3").startswith("'")
    assert _csv_safe("+15551110000") == "+15551110000"
    assert _csv_safe(None) == ""
