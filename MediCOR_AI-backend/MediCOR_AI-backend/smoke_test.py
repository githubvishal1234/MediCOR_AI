# -*- coding: utf-8 -*-
"""
End-to-end smoke test against a RUNNING API.

The pytest suite runs on in-memory SQLite with the app's routers mounted
individually. That proves the logic and proves nothing about the thing
you are about to demo: the real server, the real SQL Server database, the
real seeded data, and the auth flow end to end.

This walks the endpoints each screen calls, in the order a person clicks
them, and reports pass/fail per screen so a failure names what would
break on stage rather than which route returned a 500.

Usage:
    # terminal 1
    uvicorn app.main:app --port 8000
    # terminal 2
    python smoke_test.py

Expects the seeded demo clinic (`python -m app.seed`) and a super admin
(`python create_super_admin.py`). Edit the two credential blocks below if
yours differ. It only reads, except for the audit entries that reading a
patient record and exporting the log legitimately write.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"
results = []


def call(method, path, token=None, body=None, form=None, api_key=None):
    url = BASE + path
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["X-API-Key"] = api_key
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.headers.get("content-type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]
    except Exception as e:
        return 0, str(e)


def check(screen, label, ok, detail=""):
    results.append((screen, label, ok, detail))


# ---------------------------------------------------------------- login
CLINIC_LOGIN = {"username": "admin@healthlinkclinic.com", "password": "password123"}
SUPER_ADMIN_LOGIN = {"username": "admin@medivoice.local", "password": "DemoAdmin!2026"}

status, body = call("POST", "/auth/login", form=CLINIC_LOGIN)
check("Login", "clinic admin signs in", status == 200, f"{status} {body if status != 200 else ''}")
token = body.get("access_token") if status == 200 else None
if not token:
    for r in results:
        print(r)
    raise SystemExit("login failed - cannot continue")

status, me = call("GET", "/auth/me", token)
check("Login", "/auth/me", status == 200 and me.get("role") == "admin", str(me)[:80])
org_id = me.get("org_id")

# ---------------------------------------------------------------- dashboard
for label, path in [
    ("stats", "/dashboard/stats"),
    ("call volume", "/dashboard/call-volume"),
    ("onboarding checklist", "/onboarding/status"),
    ("notifications", "/notifications"),
]:
    s, b = call("GET", path, token)
    check("Dashboard", label, s == 200, f"{s} {str(b)[:90]}")

# ---------------------------------------------------------------- analytics (HV-9)
for label, path in [
    ("overview", "/analytics/overview?days=30"),
    ("daily", "/analytics/daily?days=30"),
    ("hourly", "/analytics/hourly?days=30"),
    ("weekday", "/analytics/weekday?days=30"),
    ("breakdowns", "/analytics/breakdowns?days=30"),
    ("per-doctor", "/analytics/doctors?days=30"),
]:
    s, b = call("GET", path, token)
    check("Analytics", label, s == 200, f"{s} {str(b)[:90]}")

# ---------------------------------------------------------------- calls + quality (AI-7)
s, calls = call("GET", "/calls", token)
check("Call Logs", "list", s == 200, f"{s}")
if s == 200 and calls:
    cid = calls[0]["id"]
    s, b = call("GET", f"/calls/{cid}", token)
    check("Call Logs", "open transcript (writes audit entry)", s == 200, f"{s} {str(b)[:70]}")
for label, path in [
    ("quality summary", "/quality/summary"),
    ("review queue", "/quality/evaluations?needs_review=true"),
    ("all evaluated", "/quality/evaluations?needs_review=false"),
]:
    s, b = call("GET", path, token)
    check("Call Quality", label, s == 200, f"{s} {str(b)[:90]}")

# ---------------------------------------------------------------- patients
s, patients = call("GET", "/patients", token)
check("Patients", "list", s == 200, f"{s}")
if s == 200 and patients:
    pid = patients[0]["id"]
    s, b = call("GET", f"/patients/{pid}", token)
    check("Patients", "open record (writes audit entry)", s == 200, f"{s}")
    s, b = call("GET", f"/ehr/patients/{pid}/history", token)
    check("Patients", "clinical history", s == 200, f"{s} {str(b)[:70]}")

# ---------------------------------------------------------------- appointments
s, b = call("GET", "/appointments", token)
check("Appointments", "list", s == 200, f"{s}")
s, docs = call("GET", "/doctors", token)
check("Appointments", "doctors", s == 200, f"{s}")
if s == 200 and docs:
    from datetime import date, timedelta
    day = (date.today() + timedelta(days=1)).isoformat()
    s, b = call("GET", f"/appointments/availability?doctor_id={docs[0]['id']}&date={day}", token)
    check("Appointments", "availability (booking modal)", s == 200, f"{s} {str(b)[:90]}")

# ---------------------------------------------------------------- clinic profile / KB
for label, path in [
    ("clinic info", "/knowledge-base/clinic-info"),
    ("services", "/knowledge-base/services"),
    ("doctors (all)", "/doctors/all"),
    ("documents", "/knowledge-base/documents"),
    ("FAQs", "/knowledge-base/faqs"),
    ("timezones", "/knowledge-base/timezones"),
]:
    s, b = call("GET", path, token)
    check("Clinic / KB", label, s == 200, f"{s} {str(b)[:80]}")

# ---------------------------------------------------------------- security (HV-1)
for label, path in [
    ("compliance", "/security/compliance"),
    ("roles", "/security/roles"),
    ("audit trail (paged)", "/security/audit-log?page=1&page_size=25"),
    ("audit filters", "/security/audit-log/actions"),
    ("filtered by action", "/security/audit-log?action=patient.viewed"),
]:
    s, b = call("GET", path, token)
    check("Security", label, s == 200, f"{s} {str(b)[:90]}")

s, b = call("GET", "/security/audit-log/export", token)
check("Security", "CSV export", s == 200 and b'occurred_at,action' in (b if isinstance(b, bytes) else b''), f"{s}")

s, page = call("GET", "/security/audit-log", token)
n = page.get("total") if isinstance(page, dict) else 0
check("Security", "audit entries actually recorded", n > 0, f"total={n}")

# ---------------------------------------------------------------- billing (HV-4)
s, plan = call("GET", "/billing/plan", token)
ok = s == 200 and "period_label" in (plan or {})
check("Billing", "plan + usage", ok, f"{s} {str(plan)[:120]}")
s, b = call("GET", "/billing/invoices", token)
check("Billing", "invoices", s == 200, f"{s}")

# ---------------------------------------------------------------- team / support
s, b = call("GET", "/users", token)
check("Team", "list", s == 200, f"{s}")
s, b = call("GET", "/support/tickets", token)
check("Support", "clinic tickets", s == 200, f"{s}")

# ---------------------------------------------------------------- agent settings + EHR
s, b = call("GET", "/agent-settings", token)
check("Agent Settings", "load", s == 200, f"{s}")
s, b = call("GET", "/ehr/integrations", token)
check("EHR", "integrations", s == 200, f"{s}")
s, b = call("GET", "/ehr/status", token)
check("EHR", "status", s == 200, f"{s} {str(b)[:80]}")

# ---------------------------------------------------------------- super admin console
# OAuth2PasswordRequestForm, same as the clinic login — form fields
# named username/password, not JSON.
s, b = call("POST", "/admin/auth/login", form=SUPER_ADMIN_LOGIN)
check("Admin Console", "super admin signs in", s == 200, f"{s} {str(b)[:90]}")
admin_token = b.get("access_token") if s == 200 else None
if admin_token:
    for label, path in [
        ("platform stats", "/admin/stats"),
        ("organizations", "/admin/organizations"),
        ("users", "/admin/users"),
        ("plans", "/admin/plans"),
        ("subscriptions", "/admin/subscriptions"),
        ("usage", "/admin/usage"),
        ("phone numbers", "/admin/phone-numbers"),
        ("support inbox", "/admin/support/tickets"),
        ("platform audit log", "/admin/audit-log"),
    ]:
        s, b = call("GET", path, admin_token)
        check("Admin Console", label, s == 200, f"{s} {str(b)[:80]}")

# ---------------------------------------------------------------- report
print()
width = max(len(s) for s, _, _, _ in results)
screen = None
failures = 0
for sc, label, ok, detail in results:
    if sc != screen:
        print(f"\n{sc}")
        screen = sc
    mark = "  PASS" if ok else "  FAIL"
    if not ok:
        failures += 1
    print(f"{mark}  {label}" + (f"   <- {detail}" if not ok else ""))
print(f"\n{len(results) - failures}/{len(results)} checks passed")

raise SystemExit(1 if failures else 0)
