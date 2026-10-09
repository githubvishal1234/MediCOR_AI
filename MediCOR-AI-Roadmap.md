# MediCOR AI — Feature Opportunities, Prioritisation & Roadmap

*Part 2 of the system analysis. Part 1 (architecture, working functionality, defects, security findings) was delivered separately.*

> **STATUS PASS — 2026-09-04.** When this document was written, nothing in it had been
> implemented. That is **no longer true**: **17 of the 32 feature items have shipped**, 2 are
> partial, and 13 are untouched, plus fixes **C2**, **C3**, **C8** and **C9**.
>
> **The tag on each `### ` heading is the authority — not this paragraph and not the tables
> below.** Counts in prose go stale the moment something ships; the tags are edited in the same
> change as the code. If they ever disagree, believe the tags and fix the prose.
>
> Every `### ` item heading now carries one of three tags:
>
> | Tag | Meaning |
> |---|---|
> | `DONE` | Verified present in the working tree. Evidence listed in *Status of this document*, below. |
> | `PARTIAL` | Some of it shipped; the remainder is described in the item body. |
> | `OPEN` | Not started. The item body still describes current behaviour accurately. |
>
> The original body text of every item is **unchanged** — including for `DONE` items, where it
> now describes the *problem as it was*, not as it is. Read the tag first. Where the body
> makes a factual claim that has since become false, an inline `**[SUPERSEDED]**` note follows it.
>
> The `SHELL EXISTS` tag has been removed from completed items, since the shell is now filled in.

---

## Status of this document

Verified against the working tree on **2026-09-04**. Read a heading's tag first; this section
explains *why* a tag says what it does, and is the place to look for the deliberate
non-decisions.

**Shipped — 17 feature items:**

| Item | Evidence in the working tree |
|---|---|
| QW-1 Clinic Profile & Services | `pages/app/ClinicProfile.jsx` (own page, not the recommended tab); six wrappers in `lib/api.js`. Tabs are query-string driven so the dashboard's setup checklist can deep-link to the section that fixes each step |
| QW-2 Doctor Management | `routers/doctors.py` full CRUD behind `require_admin`; soft delete via `active=false` |
| QW-3 Add & Archive Patient | Shipped as a **soft** archive (`status = "Archived"`) with restore |
| QW-4 Staff Booking with Availability | `getAvailability`/`bookAppointment` in the appointment modal; `AVAILABILITY_MAX_SLOTS = 32` |
| QW-5 Staff Reschedule & Cancel | `rescheduleAppointment`/`cancelAppointment` wired |
| QW-6 Ask-the-AI Test Console | On `/app/knowledge-base`, running the same `GET /knowledge-base/ask` the agent calls |
| QW-7 Role-Aware Nav & RBAC | `require_admin` on destructive and AI-configuration writes; reads open to `medical_staff`; `tests/test_qw7_rbac.py` guards the line, including that no agent-reachable route gained it |
| QW-8 Support Inbox | Clinic list on `/app/support`; platform inbox at `/admin/support` + `GET/PATCH /admin/support/tickets`; `tests/test_qw8_support_inbox.py` |
| QW-9 Notification Coverage | All three kinds via `notification_service.schedule_appointment_notification`. The call sites existed from the start but raised `DetachedInstanceError`, so nothing was ever delivered until 2026-09-03 |
| HV-1 PHI Access & Change Audit Trail | `audit_service.py` + call sites across nine routers; migration `0011`; paged/filterable `/security/audit-log` with an audited CSV export. Impersonation is now attributed to the platform in the clinic's own log |
| HV-2 Clinic Staff & Invites | `routers/users.py` invite/accept/deactivate; `pages/app/Team.jsx`, `pages/AcceptInvite.jsx` |
| HV-6 Notification Centre | `notifications` table; `notify()` writes the row *before* broadcasting and puts its id in the payload, so the live and fetched copies are one object rather than duplicates |
| HV-7 Missed-Call & Callback Queue | `components/CallbackQueue.jsx` + the callback endpoints in `routers/calls.py` |
| HV-9 Call Analytics | `analytics_service.py`, `routers/analytics.py`, `pages/app/Analytics.jsx`. Also *fixed* the two dashboard figures that were not measurements |
| AI-1 Multi-Tenant Agent Runtime | `phone_numbers`, `POST /agent/resolve-tenant`, per-call `TenantClient`, `typ="agent"` tokens. Runbook: `MULTI-TENANT-ROLLOUT.md` |
| AI-2 LLM Call Summarisation | `llm_service.py` upgrades the rule-based summary in a background task, gated on `GROQ_API_KEY` |
| AI-3 Retrieval-Augmented KB | `rag_service.py` + `embedding_service.py` (fastembed/ONNX, local). The **synthesiser** is the safety gate, not the similarity threshold |

Fixes closed alongside them: **C2** (timezone-aware scheduling), **C3** (orphan pending bookings),
**C8** (usage computed from `CallLog`, so clinic and super-admin views agree by construction),
**C9** (cross-tenant appointment references).

Also shipped and **not** on the original list: per-account login throttling, a `typ="agent"`
third token family, plan entitlement enforcement including `Subscription.status`, and a
regression suite that grew from 3 files to 19.

**Partial — 2 items, and in both cases the remainder is a decision, not typing:**

- **HV-4 Usage Metering** — metering, threshold warnings, seat/record caps, the two feature
  gates and subscription-status revocation are all live. What is left is whether an
  **over-limit clinic is cut off or billed for overage**. `over_limit` is reported and enforced
  by nothing, because hard-blocking inbound calls touches the voice path. Tagged
  `REQUIRES YOUR APPROVAL` for that reason.
- **AI-7 Call Quality** — the judge, the `call_evaluations` table, `/quality/*` and the review
  UI are all in. The **replay harness** is not, and deliberately: running a stored transcript
  against a candidate prompt needs the agent's tool loop, not just its text. It belongs with
  **AI-4**, and is the reason AI-4 should not ship before it.

**Explicitly still open, contrary to what a skim might suggest:**

- **C12 — credentials have NOT been rotated.** `SECURITY-ROTATION.md` still opens with
  "Status: NOT DONE". This remains the #1-ranked item in Section B, and only the owner can do
  it — it happens in provider consoles, not in this repo.
- **AI-5** — now `PARTIAL`. `RoutingRule` rows are read and reach the agent's prompt, so rules
  written on `/app/agent` do change a live call. The structured conditions and per-rule
  destinations remain open.
- **HV-4's stale column** — `Organization.voice_minutes_used` is now read by nothing. It is
  legacy, not a cache. Wiring anything to it re-opens C8.

---

## How to read this

Every feature below is grounded in a file, model, or endpoint that already exists in your codebase. Where a feature has **zero backend work** because the API is already built and unused, it says so. Where a feature requires touching the LiveKit/SIP/voice layer, it is flagged **`REQUIRES YOUR APPROVAL`** and is not recommended for implementation without an explicit decision from you.

Two categories are deliberately kept separate:

- **Fixes** (`C1`–`C12`, high/medium severity items) — repairs to things that are broken or claimed-but-absent. These are in Part 1 and referenced by ID here.
- **Features** (`QW-*`, `HV-*`, `AI-*`, `F-*`) — new capability.

Several "features" below are really *the honest implementation of something the UI already advertises*. Those are marked **`SHELL EXISTS`**. They are the cheapest credibility wins in the product because the UI, the routes, and often the database columns are already there.

---

# Section A — New Feature Opportunities

## A1. Quick Wins

Low effort, no architectural change, mostly wiring up capability the backend already has.

---

### QW-1 — Clinic Profile & Services Editor `DONE`

**Feature:** A dashboard screen where a clinic edits its own timings, address, phone, email, website, insurance summary, general info, and its list of services with fees.

**Problem it solves:** Today the voice agent reads clinic facts to live callers from `ClinicInfo` and `Service` — and **no UI exists to populate either**. `KnowledgeBase.jsx` never calls `clinic-info` or `services`. A clinic that signs up today has empty clinic info forever, so the agent answers "I don't have that specific detail" to the most common inbound question there is ("what time do you open?"). This is the single largest gap between what the AI *can* do and what a real customer can *configure*.

**[SUPERSEDED]** Shipped as a dedicated `/app/clinic` page (`pages/app/ClinicProfile.jsx`) rather than the recommended tab on `/app/knowledge-base`. All six API wrappers exist. A clinic can now populate its own timings, contact details, insurance summary, and services.

**How it fits the existing system:** Backend is complete. `GET/PUT /knowledge-base/clinic-info` and `GET/POST/DELETE /knowledge-base/services` already exist in `routers/knowledge_base.py` with dual auth, and `backend_client.get_clinic_info()` / `list_services()` are already called by the agent's `clinic_action` router.

**User & business value:** Turns the agent from a demo into something a clinic can actually deploy on day one. Removes the highest-frequency "the AI doesn't know" failure. Directly reduces onboarding time from "needs an engineer" to "self-serve".

**UI location:** New tab on `/app/knowledge-base` ("Clinic Profile" + "Services"), or a new `/app/clinic` page. Recommend a tab — it belongs with the AI's knowledge, not with billing.

**Frontend impact:** New tab + two forms. Add 6 wrappers to `lib/api.js` (`getClinicInfo`, `updateClinicInfo`, `listServices`, `createService`, `deleteService`). ~250 LOC.

**Backend impact:** None. Optionally add `PATCH /services/{id}` (currently create/delete only, no edit).

**Database impact:** None. `ClinicInfo` and `Service` models already exist.

**AI/LLM/Voice impact:** None to the agent code. Immediate improvement in answer quality because the data it already queries stops being empty.

**Dependencies:** None.

**Difficulty:** Easy | **Priority:** Critical

---

### QW-2 — Doctor Management `DONE`

**Feature:** Full CRUD for doctors — name, specialty, slot length, working hours, working days, consultation fee, active/inactive.

**Problem it solves:** `routers/doctors.py` is **one read-only GET endpoint, 15 lines**. Doctors exist only via `seed.py`. A real clinic cannot add its own doctors — and since `Appointment.doctor_id` drives all availability, conflict-checking, and the entire booking flow, the product is unusable for any clinic that isn't the seeded demo org.

**[SUPERSEDED]** `routers/doctors.py` is now 143 lines with full CRUD, soft-delete via `active=false`, and `require_admin` on every write — exactly as specified. `GET /doctors` (active only, for pickers) and `GET /doctors/all` (including inactive, for management) are both wired.

**How it fits the existing system:** The `Doctor` model is fully specified (`slot_minutes`, `work_start_hour`, `work_end_hour`, `work_days_csv`, `active`, `consultation_fee_label`) and already consumed by `_validate_slot` and `get_availability`. Only the write endpoints are missing.

**User & business value:** Hard blocker on selling to any second customer. Working-hours configuration is also the natural home for the per-org timezone field that fix **C2** requires.

**UI location:** New "Doctors" section — recommend on the Clinic Profile page from QW-1 so all clinic setup lives together.

**Frontend impact:** New section, doctor form modal, working-hours/day picker. ~350 LOC.

**Backend impact:** Add `POST`, `PATCH`, `DELETE` (soft-delete via `active=false`) to `routers/doctors.py`. Guard with `require_admin`. ~120 LOC.

**Database impact:** None to add CRUD. Recommend adding `Doctor.timezone` or `Organization.timezone` here as part of fix C2.

**AI/LLM/Voice impact:** None to agent code. The agent's `doctors` and `availability` actions immediately reflect real doctors.

**Dependencies:** Pairs naturally with C2 (timezone) — do them in the same change.

**Difficulty:** Easy | **Priority:** Critical

---

### QW-3 — Add & Archive Patient from the Dashboard `DONE`

**Feature:** "Add Patient" button and archive/delete action on `/app/patients`.

**Problem it solves:** `PatientFormModal.jsx` calls **only** `api.updatePatient`. `api.createPatient` and `api.deletePatient` are exported in `lib/api.js` and referenced by nothing. Staff can edit an existing patient but cannot create one. The only way a patient enters the system today is if the AI registers them over the phone.

**[SUPERSEDED]** Shipped, and shipped *better than specified*: the backend recommendation in this item — "consider changing hard `DELETE` to a soft archive" — was taken. `DELETE /patients/{id}` now sets `status = "Archived"` and retains prescriptions, interactions, and appointments; `POST /patients/{id}/restore` reverses it. No medical record is destroyed.

**How it fits the existing system:** `POST /patients` and `DELETE /patients/{id}` already exist and already auto-generate an MRN via `_generate_mrn`. The modal already has every field.

**User & business value:** Basic table-stakes CRM function. Also required for front-desk staff to pre-register walk-ins and referrals.

**UI location:** `/app/patients` header button; row overflow menu for archive.

**Frontend impact:** Add a `create` mode to `PatientFormModal` (branch on `patient ? update : create`), add the button, add a confirm dialog for delete. ~120 LOC.

**Backend impact:** None. Consider changing hard `DELETE` to a soft archive (`status = "Archived"`) — hard-deleting a patient cascades and destroys prescriptions, interactions, and appointments, which is wrong for a medical record.

**Database impact:** None (soft archive uses the existing `Patient.status` column).

**AI/LLM/Voice impact:** None.

**Dependencies:** None.

**Difficulty:** Easy | **Priority:** High

---

### QW-4 — Staff Booking with Live Availability `DONE`

**Feature:** Replace the freeform appointment form with the same validated slot picker the AI uses — pick doctor, pick date, see real open slots, book.

**Problem it solves:** `AppointmentFormModal.jsx` calls `api.createAppointment` → `POST /appointments`, which performs **no doctor validation, no working-hours check, and no conflict check**. Meanwhile `/appointments/availability` and `/appointments/book` do all three and are reachable only by the voice agent. The result today: **staff can double-book a doctor; the AI cannot.** This is also the vector for cross-tenant issue **C9**.

**[SUPERSEDED]** Both halves are fixed. Staff booking now routes through the validated `/availability` + `/book` path, and **C9 is closed** — `create_appointment` org-validates `patient_id` and `doctor_id` before they reach the model. The `MAX_SLOTS_RETURNED = 10` cap noted below is also gone, replaced by the configurable `AVAILABILITY_MAX_SLOTS` (default 32), so afternoon availability now surfaces.

**How it fits the existing system:** Zero backend work. `get_availability` and `book_appointment` are both already dual-auth (`get_org_context`), so a staff JWT already works against them.

**User & business value:** Eliminates double-booking, the most damaging operational error a clinic scheduler can make. Makes staff and AI bookings behave identically, which makes the calendar trustworthy.

**UI location:** `AppointmentFormModal` — replace free-text day/time with doctor select → date picker → slot chips.

**Frontend impact:** Rework the modal; add `getAvailability` and `bookAppointment` wrappers to `lib/api.js`. ~200 LOC.

**Backend impact:** None for the feature. Separately, `POST /appointments` should be either org-validated (fix C9) or restricted to admin-only override use.

**Database impact:** None.

**AI/LLM/Voice impact:** None. Note `MAX_SLOTS_RETURNED = 10` currently caps the scan loop, so a 9–5 day with 30-minute slots never surfaces afternoon availability — worth raising or paginating as part of this.

**Dependencies:** Fix C2 (timezone) first, or the slot times shown to staff will be wrong in the same way they're wrong for the AI.

**Difficulty:** Easy | **Priority:** Critical

---

### QW-5 — Staff Reschedule & Cancel `DONE`

**Feature:** Reschedule and cancel actions on the appointments calendar and patient profile.

**Problem it solves:** `/appointments/{id}/reschedule` and `/appointments/{id}/cancel` exist, are validated, broadcast realtime events, and trigger EHR sync — and have **no frontend wrapper at all**. Staff currently have no way to move or cancel an appointment except by editing raw fields through the unvalidated `PATCH`, which `api.js` also doesn't expose (`updateAppointment` is exported and unused). In practice, appointments cannot be cancelled from the dashboard.

**[SUPERSEDED]** `rescheduleAppointment` and `cancelAppointment` are both wired into the dashboard. The defect flagged below — "cancel notifies the patient; reschedule does not" — was fixed as part of QW-9.

**How it fits the existing system:** Both endpoints are dual-auth and already do conflict re-validation via `_validate_slot(..., exclude_appointment_id=...)`.

**User & business value:** Roughly 20–30% of clinic scheduling work is changes, not new bookings. Without this the dashboard is not usable as a scheduler.

**UI location:** Appointment card overflow menu on `/app/appointments`; Appointments tab on `/app/patients/:id`.

**Frontend impact:** Two `api.js` wrappers, a reschedule modal reusing the QW-4 slot picker, a cancel confirm. ~180 LOC.

**Backend impact:** None for reschedule. **Cancel currently notifies the patient; reschedule does not** — that's a defect worth fixing in the same change (see QW-9).

**Database impact:** None.

**AI/LLM/Voice impact:** None.

**Dependencies:** QW-4 (shares the slot picker).

**Difficulty:** Easy | **Priority:** High

---

### QW-6 — "Ask the AI" Test Console `DONE`

**Feature:** A search/ask box on the Knowledge Base page that runs a question through the exact same `/knowledge-base/ask` path the voice agent uses, and shows the answer, the matched FAQ, and the source.

**Problem it solves:** A clinic has no way to find out what the AI will tell its patients until a patient calls. `/knowledge-base/ask` and `/knowledge-base/search` both exist and neither has a frontend wrapper. Given how loose the current matching is (single-keyword overlap, threshold `>= 1`), the inability to test is compounding an existing safety risk.

**How it fits the existing system:** `ask_knowledge_base` returns `{answer, matched_question, source}` — exactly the shape a test console needs. Dual auth means a staff JWT works.

**[SUPERSEDED]** Shipped 2026-09-03 at the top of `/app/knowledge-base`, as specified. `askKnowledgeBase` and `searchKnowledgeBase` added to `lib/api.js`. The result panel shows the answer verbatim, which FAQ matched, and — when one did — an explicit warning that matching is single-keyword and the AI will read that answer to callers as-is. That makes the clinical-safety risk this item describes **visible to the clinic** rather than only documented here; it does not fix it. AI-3 still does.

**User & business value:** Trust. Lets a clinic verify AI answers before go-live and after every FAQ edit. Also the natural surface to later show RAG citations (see AI-3).

**UI location:** Top of `/app/knowledge-base`.

**Frontend impact:** One input, one result card, two `api.js` wrappers. ~100 LOC.

**Backend impact:** None.

**Database impact:** None.

**AI/LLM/Voice impact:** None now. Becomes the primary QA surface once real RAG lands.

**Dependencies:** None. Strongly complements AI-3.

**Difficulty:** Easy | **Priority:** High

---

### QW-7 — Role-Aware Navigation & Real RBAC `DONE`

**Feature:** Hide nav items a role can't use, and enforce roles on the backend endpoints that currently don't check.

**Problem it solves:** `AppShell.jsx`'s `NAV` array is identical for every role — a `medical_staff` user sees Billing, Security, and EHR Sync, clicks them, and gets a 403. More seriously, `require_admin` appears in **only 3 of 16 routers** (ehr, billing, support). Any authenticated user regardless of role can delete patients, rewrite agent settings, and delete knowledge base documents.

**[SUPERSEDED — 2026-09-03]** Done. 17 routes across `patients`, `agent_settings` and `knowledge_base` moved to `require_admin`, and `AppShell.jsx` filters admin-only destinations out of the sidebar.

The line drawn, since the item didn't specify one: **reads stay open to medical_staff** (they cannot do their job otherwise), while **destructive actions and anything that changes what the AI says to callers** are admin-only — archiving a patient, deleting a prescription, and every write to the greeting, routing rules, FAQs, documents, clinic info and services.

The risk this item flagged — "verify no agent path breaks" — was the real work. Only routes using `get_current_user` were touched; every `get_org_context` route the voice agent reaches was left alone, and `tests/test_qw7_rbac.py` asserts the agent can still read patients, clinic info, services, FAQs and `/ask`, and still register a caller as a patient. If someone later gates one of those, that test fails before production does.

Nav filtering is presentation only — the pages render an "admin access required" state when opened directly, and enforcement is entirely server-side. The Security page's role descriptions (`ROLE_DETAIL` in `security_compliance.py`) describe a permission model the code does not implement.

**How it fits the existing system:** `deps.require_admin` already exists and works. `UserRole` enum already has three values. This is applying existing machinery consistently.

**User & business value:** Least-privilege is a baseline expectation for anything handling PHI, and this is the kind of thing that surfaces in a security questionnaire during a healthcare sales cycle.

**UI location:** `AppShell.jsx` NAV filtering by `user.role`.

**Frontend impact:** Add a `roles` key to each NAV entry, filter on render. ~30 LOC.

**Backend impact:** Add role dependencies across `patients` (delete → admin), `agent_settings` (write → admin), `knowledge_base` (write/delete → admin), `appointments` (delete → admin). Consider a `require_staff` dependency for read-heavy routes. ~60 LOC across routers.

**Database impact:** None.

**AI/LLM/Voice impact:** None — the agent authenticates by API key through `get_org_context`, which does not go through `require_admin`. Verify no agent path breaks.

**Dependencies:** Should land with HV-2 (staff management), since there's currently no way to create a non-admin user anyway.

**Difficulty:** Easy | **Priority:** High

---

### QW-8 — Support Ticket Inbox & Reachable Support Page `DONE`

**Feature:** Add Support to the nav; let admins see and track the tickets their staff submit.

**Problem it solves:** `/app/support` has a route and a page — but **no nav entry anywhere**, so it's unreachable through the UI. And `GET /support/tickets` (admin-only) has no `api.js` wrapper, so submitted tickets are unviewable by anyone. Tickets go into a black hole. There is also no super-admin view of tickets across orgs.

**[PARTIALLY SUPERSEDED — updated 2026-09-03]** The nav entry exists (`AppShell.jsx`) and the ticket inbox now works: `listSupportTickets` was added to `lib/api.js` and `/app/support` renders the clinic's own tickets with status/priority chips, refreshing after a submit. The endpoint is admin-only, so the section hides itself on a 403 rather than showing a medical_staff user an error.

**[SUPERSEDED — 2026-09-04]** Both remaining halves are closed.

The **cross-org view** is `GET /admin/support/tickets` plus `/admin/support` — one inbox across every clinic, filterable to one org or one status, rather than a section per organization. A super admin can move a ticket Open → In Progress → Resolved, which writes a `support_ticket.status_change` row to the super-admin audit log like every other cross-org action. `AdminOrgDetail` links through pre-filtered instead of duplicating the list, so the triage controls exist in exactly one place. Triage is deliberately the existing `status` column and nothing more: it was already there, defaulted to `"Open"`, and was never written again by anything. `assigned_to` / `resolved_at` would need a migration and a second person's workflow before earning their keep. `tests/test_qw8_support_inbox.py` pins the org scoping in both directions — the platform inbox rejects a clinic token, and the clinic endpoint still returns only its own rows.

One detail worth keeping: the submitter is resolved with a separate lookup rather than a join, because `SupportTicket.user_id` is nullable and the ticket outlives the user. A join would have silently dropped a ticket whose author had left the clinic — which is exactly the ticket somebody still needs to answer.

For **`/support/docs`**, the choice this item offered was "publish the docs and add real hrefs, or drop the section". Neither, in the end: the rows stay (they say what is coming) but stopped being `<button>` elements with a chevron and a hover state, because that read as a link and did nothing. They are now plain rows with a "Soon" chip, under a line saying the guides are being written. Add hrefs the day the docs exist. **Still dead on this page and not touched:** the "Browse Docs" and "Call Us" buttons in the top cards, which need a URL and a phone number only the owner has.

**How it fits the existing system:** `SupportTicket` model, create and list endpoints all exist.

**User & business value:** Small, but it's a broken promise currently shipping in the product. Cross-org ticket visibility is also a genuine super-admin gap.

**UI location:** Nav entry; ticket list on `/app/support`; new section on the super-admin org detail page.

**Frontend impact:** Nav entry, one `api.js` wrapper, ticket list. ~120 LOC.

**Backend impact:** Add a super-admin endpoint to list tickets across orgs (~40 LOC). Also `/support/docs` currently returns titles and descriptions with **no URLs** — the links are unclickable.

**Database impact:** None. Consider adding `SupportTicket.resolved_at` / `assigned_to` if you want real triage.

**AI/LLM/Voice impact:** None.

**Dependencies:** None.

**Difficulty:** Easy | **Priority:** Medium

---

### QW-9 — Complete the Notification Coverage `DONE`

**Feature:** Send patient confirmations for reschedules and for staff-created bookings, not just AI bookings and cancellations.

**Problem it solves:** `notification_service.notify_appointment_booked` is called from **only** `/appointments/book`. `reschedule_appointment` sends **nothing** — a patient whose appointment is moved is never told, so they arrive at the old time. Staff-created appointments via `POST /appointments` and `verify_pending_booking` also send nothing.

**[SUPERSEDED — corrected 2026-09-03]** `notify_appointment_rescheduled` was added and all three call sites exist. **But an earlier status pass marked this `DONE` on the strength of those call sites alone, and that was wrong — the feature did not work.**

All three passed **live ORM instances** into `BackgroundTasks`:

```python
background_tasks.add_task(notification_service.notify_appointment_booked, patient, appt, doctor, ...)
```

By the time the task ran, the router had committed — SQLAlchemy's default `expire_on_commit=True` expires every attribute — and `get_db` had closed the session. The instances were detached, so the first attribute read (`if patient.email`) tried to refresh from a dead session and raised `DetachedInstanceError`. The booking had already returned 200, so the failure was invisible: **no patient ever received a confirmation, and only a server-log traceback recorded it.**

Fixed by adopting the pattern `ehr_service.py` already used for this exact reason: `schedule_appointment_notification(background_tasks, kind, appointment_id, org_id, previous_when=None)` passes ids only, opens a fresh session inside the task, and re-queries. Two regression tests that had been failing on this now pass.

**The lesson for the rest of this document:** a call site existing is not evidence a feature works. Later status passes should exercise the path, not grep for it.

**Still open from this item:** SMTP/Twilio credentials remain platform-global, so every clinic's patients receive messages from one shared sender identity — see HV-3.

**How it fits the existing system:** `notification_service.py` already has email + WhatsApp channels, already degrades silently when unconfigured, and is already invoked via `BackgroundTasks`. This is adding three call sites and one new message template.

**User & business value:** A silent reschedule is a guaranteed no-show plus an angry patient. Highest-value-per-line-of-code item in this section.

**UI location:** None (server-side).

**Frontend impact:** None.

**Backend impact:** Add `notify_appointment_rescheduled` (~30 LOC) and call it from `reschedule_appointment`, `create_appointment`, and `verify_pending_booking`.

**Database impact:** None now. See HV-3 for delivery tracking.

**AI/LLM/Voice impact:** None.

**Dependencies:** Note that SMTP/Twilio credentials are currently **platform-global**, so every clinic's patients receive messages from one shared sender identity. Per-org sender config belongs with HV-3.

**Difficulty:** Easy | **Priority:** High

---

## A2. High-Value Features

Real new capability. Medium effort. These are what turn the product into something a clinic pays for and a platform you can operate.

---

### HV-1 — PHI Access & Change Audit Trail `DONE`

**Feature:** Actually write audit entries for logins, failed logins, patient record access, appointment changes, API key lifecycle, settings changes, data export, and super-admin impersonation.

**Problem it solves:** `AuditLogEntry` is written by exactly two things: `seed.py` and `ehr_service.py`. The Security page renders **seeded demo rows**. Nothing logs who viewed a patient record. Meanwhile the same page displays "BAA Signed by Admin · v2.4" and a data retention policy. A product that displays HIPAA posture while having no PHI access log has a compliance problem, not a feature gap. Super-admin impersonation compounds this: it mints a normal 24-hour token, logs only to `SuperAdminAuditLog`, and **the clinic can never see that its records were accessed**.

**[SUPERSEDED — 2026-09-04]** Shipped as `app/audit_service.py` plus call sites across `auth`, `patients`, `appointments`, `calls`, `ehr`, `agent_settings`, `knowledge_base`, `users` and `admin`; migration `0011` adds the context columns; `tests/test_hv1_audit_trail.py` covers it.

The write path went in as the small helper this item suggested rather than a dependency, for one reason: a dependency fires on every request to a route, but what needs recording is the *outcome* — a 404 on `GET /patients/{id}` must not leave a row saying the record was accessed, and a rolled-back booking must not leave one saying it was made. `record()` therefore adds to the caller's session without committing, so the entry lands in the same transaction as the change it describes.

**Six decisions worth keeping:**

1. **Reads are logged at the individual-record level, not the list level.** `GET /patients/{id}` writes an entry; `GET /patients` does not. A row per dashboard render would bury the individual reads a breach investigation is looking for.
2. **The voice agent is a first-class actor.** It authenticates through `get_org_context` like staff, so `actor_from_context` labels it "AI Voice Agent" and the per-call PHI access trail this item predicted comes for free. The one exception is `GET /calls/{id}`: the agent reading back the call it is itself conducting is not a disclosure, and logging it would add a row per poll per call.
3. **Impersonation is now visible to the clinic**, which is the compounding problem this item named. The token minted by `POST /admin/organizations/{id}/impersonate` carries an `imp` claim, `deps._attach_impersonator` resolves it, and every entry written during that session reads `MediCare Platform Admin (…) acting as …` with `actor_type="platform"`. The impersonation event itself is mirrored into the clinic's log, not only ours. Adding `imp` does **not** create a fourth token family — `decode_access_token` rejects on `typ`, which is still the only barrier between the three.
4. **Failed logins are recorded against known accounts only.** `audit_log.org_id` is NOT NULL, so an attempt against an address matching no account has no clinic to file it under — and writing it anywhere visible would turn this page into the account-enumeration oracle that the deliberately-identical 401 exists to prevent. Unknown addresses stay in the application log, where the rate limiter already tracks them.
5. **Changes record field names, never values.** `audit_service.changed_fields` names what changed. Storing old and new values would make the audit log a second copy of the patient record — with a longer retention period, on a page more people can read.
6. **It is fail-open, and that is a choice rather than an oversight.** `record()` never raises, so a database fault can let an action succeed unlogged. Fail-closed is the stricter posture; refusing to answer a phone call because an audit row would not insert is the wrong trade for a clinic. The failure is logged at exception level.

**Two fabricated compliance claims were removed while here**, because they are the other half of what made this a compliance problem rather than a feature gap:

* `ComplianceOut.hipaa_verified` defaulted to `True` in the schema and was never set by the router, so the API asserted that every clinic — including one thirty seconds old — was HIPAA verified. It is now derived from whether a security audit date has actually been recorded.
* `Organization.baa_status` defaulted to `"Signed by Admin · v2.4"`, so every clinic that registered was recorded as holding a Business Associate Agreement it had never seen. New orgs start `"Not signed"`. **Existing rows still hold the old string** — only the owner can correct those.
* `seed.py`'s five demo audit rows are gone. With a real write path they are fabricated compliance records on the page a clinic would show an auditor, indistinguishable from the real rows beside them.

**Still open:** the encryption values on that page (`AES-256`, `TLS 1.3`) remain schema defaults that nothing verifies — the app cannot tell whether SQL Server TDE is on. The green ticks beside them are gone and the card now says it is stating intended configuration, but making them true needs a deployment fact the application does not hold. Separately, the **CSV export in `CallLogs.jsx` builds its file entirely in the browser**, so a staff member exporting call transcripts leaves no audit entry; closing that needs an endpoint, not a call site. And retention — `Organization.data_retention_years` is displayed and enforced by nothing, so this table grows without bound.

**How it fits the existing system:** The model, the org-scoped endpoint (`GET /security/audit-log`), and the UI page all exist. What's missing is the write path. A FastAPI dependency or a small `audit.record(db, org_id, action, who, status)` helper called from routers is the least invasive approach.

**User & business value:** Gates enterprise/hospital sales entirely. Also the foundation for breach investigation and for satisfying a BAA's audit obligations.

**UI location:** `/app/security` (page exists); add filtering, date range, pagination, and CSV export.

**Frontend impact:** Filters, pagination, export on the existing page. ~200 LOC.

**Backend impact:** An audit helper module plus call sites across auth, patients, appointments, calls, ehr, agent_settings, knowledge_base, admin. Add pagination to `GET /security/audit-log` (currently a flat `limit=50`). ~300 LOC.

**Database impact:** Add `actor_user_id`, `ip_address`, `user_agent`, `target_type`, `target_id` to `AuditLogEntry` — mirror what `SuperAdminAuditLog` already has. Index on `(org_id, occurred_at)`. **Also: mirror impersonation events into the clinic's own audit log**, not just the platform one.

**AI/LLM/Voice impact:** Agent actions arrive via `get_org_context` with `actor="service"` — log those as the AI agent, which gives you a per-call PHI access trail for free.

**Dependencies:** None technically. Pairs with the retention job in Phase 5.

**Difficulty:** Medium | **Priority:** Critical

---

### HV-2 — Clinic Staff & Invite Management `DONE`

**Feature:** Clinic admins invite staff by email, assign roles, deactivate users, and reset passwords.

**Problem it solves:** There is **no `/users` router**. A clinic admin cannot add a receptionist. The only user creation path in the entire product is `POST /auth/register`, which creates a *new organisation* — so a second staff member gets their own separate clinic. Only super admin can modify users, via `PATCH /admin/organizations/{org_id}/users/{user_id}`. This makes the three-role model (`admin`, `medical_staff`, `ai_agent`) unusable in practice.

**How it fits the existing system:** `User`, `UserRole`, `require_admin`, and password hashing all exist. Needs a new org-scoped router.

**[SUPERSEDED — 2026-09-03]** Shipped. `app/routers/users.py` (list / invite / resend / update / deactivate, all admin-only and org-scoped), `POST /auth/accept-invite` for the one unauthenticated step, `/app/team` in the dashboard, and a public `/accept-invite` page.

Two decisions worth recording, both departing from this item's suggestions:

* **No `Invitation` table.** An invited person is a real `users` row from the moment the invite is sent — inactive, with an unusable password. The existing unique constraint on `email` then prevents a duplicate invite racing a duplicate signup, `get_current_user` already refuses inactive users so an unaccepted invite cannot authenticate, and there are no orphan rows to reconcile.

* **The invite token is NOT a JWT**, unlike the other three token families. An invite travels in a URL, gets forwarded, and sits in browser history; a JWT there would be an unrevokable bearer credential. It is an opaque random value stored only as SHA-256 — revocable by clearing the column, rotated on resend, and single-use.

**`User.email` remains globally unique**, so the same person still cannot hold accounts at two clinics — this item's suggested `UniqueConstraint(org_id, email)` was deliberately not applied, because `POST /auth/login` resolves a user by email with no org in hand and that change would make login ambiguous. The invite endpoint handles the resulting cross-org collision by refusing with "that email address is not available", which does not confirm an account exists elsewhere.

Guards: an admin cannot demote or deactivate themselves, and the last active admin in an org cannot be demoted, deactivated or removed — a clinic locked out of its own dashboard has no self-service recovery. 15 tests in `tests/test_hv2_staff_management.py`.

**User & business value:** A clinic is a team. Without this the product supports exactly one login per customer. Also unblocks QW-7's RBAC, which is meaningless while every user is an admin.

**UI location:** New `/app/team` page, or a "Users" tab on `/app/security` (which already renders role counts from `GET /security/roles`).

**Frontend impact:** New page, invite modal, role selector, deactivate confirm. ~400 LOC.

**Backend impact:** New `routers/users.py` — list, invite, update role, deactivate, resend invite. Invite flow needs a token (reuse the JWT machinery with a `typ="invite"` claim, consistent with how super-admin tokens are separated). ~250 LOC.

**Database impact:** Add an `Invitation` table (`org_id`, `email`, `role`, `token_hash`, `expires_at`, `accepted_at`), or add `User.invite_token_hash` + `User.invited_at` if you'd rather not add a table. **Note `User.email` is currently globally unique** — the same person cannot exist at two clinics. Consider `UniqueConstraint(org_id, email)` instead, though that changes login lookup semantics and needs care.

**AI/LLM/Voice impact:** None.

**Dependencies:** SMTP config (already in `settings`). QW-7 for role enforcement.

**Difficulty:** Medium | **Priority:** Critical

---

### HV-3 — Appointment Reminders & No-Show Reduction `OPEN`

**Feature:** Automated reminders at configurable intervals (e.g. 24h and 2h before), across email and WhatsApp, with delivery tracking and patient confirm/cancel response handling.

**Problem it solves:** No reminders exist. More fundamentally, **there is no job scheduler anywhere in the codebase** — no Celery, no APScheduler, no cron. Every background operation is a per-request `BackgroundTasks` callback, which cannot do "run at 9am tomorrow". This is the single biggest missing commercial capability: no-shows cost outpatient clinics 10–30% of capacity, and reminder systems are the most-cited ROI justification for buying software like this.

**How it fits the existing system:** `notification_service.py` already has both channels and a fire-and-forget philosophy. `Appointment.start_at` is populated by the validated booking path. What's missing is the scheduler and a delivery record.

**User & business value:** The clearest measurable ROI story you can put in front of a clinic ("we recovered X appointments last month"). Also unlocks HV-8 (waitlist backfill) and AI-8 (outbound campaigns), both of which need the same scheduler.

**UI location:** Reminder settings on the Clinic Profile page (QW-1); per-appointment delivery status on the appointment card and patient profile.

**Frontend impact:** Settings form; delivery status chips. ~200 LOC.

**Backend impact:** **Introduce a scheduler.** Given the single-process constraint today, APScheduler with a database job store is the lowest-friction option; Celery + Redis is the right answer if you're also doing the Redis work in Phase 5. Add a reminder dispatcher that queries due appointments, sends, and records the result. Add inbound webhook handling if you want patients to confirm by reply. ~400 LOC.

**Database impact:** New `NotificationLog` table (`org_id`, `patient_id`, `appointment_id`, `channel`, `template`, `status`, `provider_message_id`, `sent_at`, `error`). New `ReminderSettings` per org, or extend `ClinicInfo`. Add `Patient.contact_consent` / `opt_out_at` — **patient messaging consent is a legal requirement, not a nicety**.

**AI/LLM/Voice impact:** None initially. Later, an unconfirmed reminder can trigger an outbound AI call (AI-8) — but that touches the voice layer and needs your approval.

**Dependencies:** Scheduler infrastructure. Per-org sender identity (currently platform-global SMTP/Twilio credentials — every clinic's patients see the same sender).

**Difficulty:** Medium | **Priority:** Critical

---

### HV-4 — Real Usage Metering & Plan Enforcement `PARTIAL` `REQUIRES YOUR APPROVAL`

**Feature:** Increment voice minutes from actual call duration; enforce plan limits; warn at thresholds; block or degrade gracefully at the cap.

**Problem it solves:** **`Organization.voice_minutes_used` is never incremented anywhere in the codebase.** It's set once in `seed.py` to 3450 and read by three surfaces. The Billing page will display "3,450 / 5,000 minutes" forever regardless of call volume. Meanwhile `admin_usage.py` computes real usage from `sum(duration_seconds)/60` — so **super admin and the clinic see two different, contradictory numbers** (finding C8). Separately, `Plan.user_limit`, `patient_limit`, `ehr_access`, and `knowledge_base_access` are stored, displayed in the admin UI, and **never checked at request time**, and `Subscription.status` is never consulted on any clinic-facing request. Cancelling a subscription does nothing.

**[PARTIALLY SUPERSEDED — 2026-09-03]** `app/usage_service.py`. The metering half is done; deliberate blocking of over-limit calls is not.

**C8 is closed by computing, not counting.** This item offered two options — treat the column as a cached rollup, or compute per-period and drop it. Computing won: a cached counter and a computed total can drift, and when they do there is no way to tell which is right. Voice minutes are now derived from `CallLog` per billing month and the stale column is read nowhere, so the clinic's Billing page and the super-admin usage page agree **by construction**. `test_the_clinic_and_super_admin_now_agree` pins that shut.

The billing period is a calendar month in the **clinic's own timezone**, not UTC — otherwise a clinic at UTC+5:30 sees its month roll over at 5:30 AM on the 1st and a call just after local midnight is billed to the previous month.

**Threshold warnings fire on the crossing**, comparing usage with and without the call that just ended. Warning on "currently over" would fire once per call for the rest of the month and train staff to ignore it.

**Plan limits are now enforced**, with one deliberate exception. `user_limit` (active users only — a departed colleague should not consume a seat), `patient_limit`, and the `ehr_access` / `knowledge_base_access` feature gates are all checked at request time. Feature gates **default to allowed when no plan is assigned**, because most orgs have no `Subscription` row and denying them would have broken every existing clinic on deploy.

**[Corrected 2026-09-04]** When the paragraph above was written, `usage_service.has_feature` existed and was tested but was **called by no router** — the seat and record caps were live, the two feature gates were not. They are wired now, and how they are wired is the part worth preserving:

- **Staff writes 403 with a message naming the plan.** Connecting an EHR (`PATCH /ehr/integrations/{id}`) and the three writes that add to what the AI knows (`POST /knowledge-base/documents`, `/sources`, `/faqs`).
- **Agent-reachable paths degrade instead.** `rag_service.answer_question` returns `source: "none"` — byte-identical to its nothing-matched reply — so the agent offers a transfer and the caller never hears that the clinic has a billing problem. `ehr_service.get_active_integration` returns None, so sync, history and status all report the `local_only` state an unconfigured org already had. The gate lives inside that one function precisely so no future EHR endpoint can forget it. `test_the_voice_agent_is_never_403d_by_a_plan_gate` is the regression guard.
- **`clinic-info` and `services` are never gated.** Every plan needs the agent to know what time the clinic opens; an agent that cannot answer that is broken, not downgraded.

**`Subscription.status` is now consulted too**, which closes the other half of this item's complaint that cancelling a subscription does nothing. Three cases, and the distinction between the first two is the whole point: no subscription row → allowed (the grandfather rule, or enforcement shipping would have locked out every existing clinic); `canceled` → denied (somebody decided, and falling through to the grandfather rule would leave a cancelled clinic with *more* access than a paying Starter one); `active` / `past_due` → whatever the plan says. `past_due` deliberately keeps access — cutting a clinic's phone AI off over a late invoice is a dunning decision, and dunning belongs with HV-5 where a payment provider defines "late".

The **Billing page** stopped making three claims it could not support: hardcoded copy describing every plan as including "premium EHR integrations and dedicated support" (false for the seeded Starter plan, and now enforced as false), a status chip hardcoded to a green "Active" even for a cancelled subscription, and an "Upgrade Plan" button whose only possible outcome was the 501 described below. It now renders the real plan flags, the real subscription status, the billing period in the clinic's own timezone, and an over-limit state — the usage bar was green at every value including 140%.

The exception: `POST /patients` is dual-auth and the voice agent calls it to register a caller mid-conversation, so the patient limit is checked **only for staff** (`ctx.actor == "user"`). A clinic over its cap has a billing problem; failing a patient on a live phone call is the wrong way to raise it.

**Also fixed, and it was a live hole rather than a missing feature:** `POST /billing/upgrade` used to set `plan = "Enterprise"` and `voice_minutes_limit = 15000` and commit, with no payment of any kind — any clinic admin could click Upgrade and get Enterprise free. It now returns 501 and points at the super-admin path, which involves a human agreeing a price.

**Still open — needs your decision, not more code:** whether an over-limit clinic is cut off or billed for overage. `over_limit` is reported and acted on by nothing, because hard-blocking inbound calls touches the voice path. That is the `REQUIRES YOUR APPROVAL` half of this item.

**How it fits the existing system:** The natural hook is `update_call` in `routers/calls.py`, which already runs when a call moves to a terminal status and already has `duration_seconds`. Limit enforcement fits as a dependency alongside `get_org_context`.

**User & business value:** Without this, plans are decoration and there is no revenue mechanism. It's also the foundation for usage-based pricing, which is the standard model for voice AI.

**UI location:** `/app/billing` (page exists); usage warning banner in `AppShell`; super-admin usage page (exists).

**Frontend impact:** Usage bar wired to real data, threshold banners. ~150 LOC.

**Backend impact:** Increment on call finalisation; a `check_plan_limit(feature)` dependency; resolve the two-sources-of-truth problem by making `admin_usage`'s computed figure authoritative and treating the column as a cached rollup. ~250 LOC.

**Database impact:** Add `Organization.usage_period_start` and `voice_minutes_used_period` for monthly reset, or compute per-period from `CallLog` and drop the column entirely (cleaner — one source of truth).

**AI/LLM/Voice impact:** Over-limit behaviour needs a decision: hard-block inbound calls, or let them through and bill overage? Blocking touches the voice layer — **`REQUIRES YOUR APPROVAL`**. Overage-billing does not.

**Dependencies:** HV-5 for the commercial half.

**Difficulty:** Medium | **Priority:** Critical

---

### HV-5 — Payments & Self-Serve Subscription `SHELL EXISTS` `OPEN`

**Feature:** Stripe integration — checkout, subscription lifecycle, real invoices, payment method management, dunning.

**Problem it solves:** `POST /billing/upgrade` sets `plan = "Enterprise"`, `voice_minutes_limit = 15000`, and commits. There is no payment. `payment_label = "Visa ending in 4242"` and `payment_expires = "12/25"` are **hardcoded model defaults**. Invoices are three seeded rows from 2023. Any clinic admin can click Upgrade and get Enterprise for free.

**How it fits the existing system:** `Plan` and `Subscription` (super-admin managed) are well-modelled and already keep `Organization.plan` / `voice_minutes_limit` in sync — that's the right place to attach Stripe price IDs.

**User & business value:** There is currently no way to collect money. That's the whole commercial layer.

**UI location:** `/app/billing`; plan management on the super-admin plans page (exists).

**Frontend impact:** Stripe Checkout redirect or Elements, payment method UI, invoice list from real data. ~400 LOC.

**Backend impact:** Stripe SDK, checkout session creation, **webhook handler with signature verification** (`invoice.paid`, `customer.subscription.updated`, `deleted`, `payment_failed`), invoice sync. ~500 LOC.

**Database impact:** Add `Plan.stripe_price_id`, `Organization.stripe_customer_id`, `Subscription.stripe_subscription_id`, `current_period_end`. Extend `Invoice` with `stripe_invoice_id` and a hosted URL.

**AI/LLM/Voice impact:** None directly. Past-due handling should degrade the same way as HV-4's limit handling.

**Dependencies:** HV-4 (metering). Stripe account.

**Difficulty:** Medium-Hard | **Priority:** High

---

### HV-6 — Persistent Notification Centre & Staff Task Inbox `DONE`

**Feature:** Persist notifications, mark read/unread, filter, and turn actionable ones (transfer requested, AI booking needs verification, call failed) into a task queue.

**Problem it solves:** `realtime.notify()` pushes over WebSocket only. `RealtimeProvider` holds notifications in React state — **a page refresh wipes them, and anything that happens while nobody has the dashboard open is lost forever**. A transfer-to-nurse request at 3am simply doesn't exist by morning. There is no replay, no history, no assignment.

**How it fits the existing system:** `realtime.notify()` is already the single choke point — persisting there covers every existing event with no router changes. `NotificationBell` in `AppShell.jsx` already renders the list.

**[SUPERSEDED — 2026-09-03]** Shipped, and the choke-point observation held exactly: persistence went into `realtime.notify()` and **no router that raises a notification changed**.

Two details worth recording:

* **The stored row's id is included in the WebSocket payload.** Without it, a live notification and the same one fetched from history would be indistinguishable duplicates after a reload, and neither could be marked read. `RealtimeProvider` now de-duplicates on that id and falls back to a synthetic `local-` id when persistence failed.
* **`notify()` is now defensive at its own level**, not only inside the persistence helper. Every caller invokes it *after* committing its own work, so anything raised there would turn a succeeded booking into a 500 — and would tell the voice agent its call update failed when it had not. A test pins this.

Delivered as the notification bell backed by history rather than a separate `/app/inbox` page; the task-queue half of this item (assignment, resolution) is **not** built.

**User & business value:** Turns the dashboard from a live monitor into a work queue. Nothing gets dropped. This is what makes the product usable by a clinic that doesn't stare at the screen all day.

**UI location:** Existing bell in `AppShell`; new `/app/inbox` for the full queue.

**Frontend impact:** Fetch history on mount, merge with live events, read/unread, filters, inbox page. ~350 LOC.

**Backend impact:** Persist in `notify()`; add `GET /notifications` (paginated), `PATCH /notifications/{id}/read`, `POST /notifications/read-all`. ~180 LOC.

**Database impact:** New `Notification` table (`org_id`, `level`, `title`, `message`, `data_json`, `read_at`, `assigned_to`, `resolved_at`, `created_at`). Index `(org_id, created_at)`.

**AI/LLM/Voice impact:** None. Becomes much richer once AI-2 produces real summaries to attach.

**Dependencies:** None.

**Difficulty:** Medium | **Priority:** High

---

### HV-7 — Missed-Call & After-Hours Callback Queue `DONE`

**Feature:** Capture calls that fail, go unanswered, or arrive outside clinic hours into a callback queue with caller number, timestamp, and any partial transcript.

**Problem it solves:** `CallLog.status` already supports `failed` and `no_answer`, and `update_call` already fires a warning-level notification for them — but there is **no queue, no follow-up workflow, and no visibility beyond a transient toast**. A missed call at a clinic is a lost patient, and right now nothing surfaces them as a to-do.

**How it fits the existing system:** Pure read-side feature over data the agent already writes. Requires nothing from the voice layer.

**[SUPERSEDED — 2026-09-03]** Shipped, and it did require nothing from the voice layer. Three nullable columns on `call_logs` (`callback_status`, `callback_note`, `callback_updated_at`), `GET /calls/callbacks/queue`, `PATCH /calls/{id}/callback`, and a collapsible queue above the call log.

Design points:

* A call joins the queue automatically **only on the transition** into `failed`/`no_answer`, and only if no `callback_status` is already set — so re-finalising a call (an agent retry, say) cannot reset work someone has already actioned. That is the regression the tests guard hardest.
* The queue defaults to `pending` + `contacted` and is **oldest-first**: it is a to-do list, so the longest-waiting caller sits at the top and resolved items drop out.
* Staff-only via `get_current_user`. The agent has no reason to read or advance the queue, so it is deliberately not exposed through `get_org_context`.

**User & business value:** Directly recovers revenue that is currently walking away. Easy to demo, easy to quantify.

**UI location:** New section on `/app/calls`, or a filter tab; feeds the HV-6 inbox.

**Frontend impact:** Filter tab, callback-status actions, "mark contacted". ~200 LOC.

**Backend impact:** Filtering on `CallLog.status`; a `callback_status` field and a small state machine. ~120 LOC.

**Database impact:** Add `CallLog.callback_status` (`pending` / `contacted` / `resolved`), `callback_assigned_to`, `callback_note`.

**AI/LLM/Voice impact:** None for the queue itself. Auto-calling back is AI-8 and touches the voice layer — **`REQUIRES YOUR APPROVAL`**.

**Dependencies:** HV-6 for inbox integration.

**Difficulty:** Easy-Medium | **Priority:** High

---

### HV-8 — Cancellation Waitlist & Auto-Backfill `OPEN`

**Feature:** Patients who wanted an unavailable slot join a waitlist; when a cancellation frees that slot, they're notified automatically in order.

**Problem it solves:** When `cancel_appointment` runs, the slot silently returns to the availability pool and nobody knows. Meanwhile the agent's `availability` action returns "no slots" to callers who would happily take a cancellation. Recovered cancellations are pure margin.

**How it fits the existing system:** `cancel_appointment` is the single hook — it already broadcasts `appointment.cancelled`. `get_availability` already knows the slot map. `PendingBooking` shows the pattern for a lightweight staged-booking table.

**User & business value:** Recovers otherwise-lost capacity with zero staff effort. Very strong demo. Distinctive versus generic scheduling tools.

**UI location:** Waitlist section on `/app/appointments`; "add to waitlist" in the booking modal.

**Frontend impact:** Waitlist list, add/remove, manual-offer action. ~250 LOC.

**Backend impact:** Waitlist CRUD; a matcher triggered on cancellation; a hold window so two patients don't claim the same slot. ~300 LOC.

**Database impact:** New `Waitlist` table (`org_id`, `patient_id`, `doctor_id`, `preferred_window_start/end`, `status`, `created_at`, `offered_at`, `hold_expires_at`).

**AI/LLM/Voice impact:** Add a `waitlist_join` action to the agent's `clinic_action` router so the AI can offer it when no slots are free — **`REQUIRES YOUR APPROVAL`** (touches `agent.py`). The staff-side feature works without it.

**Dependencies:** HV-3 (notification delivery + scheduler for hold expiry).

**Difficulty:** Medium | **Priority:** Medium-High

---

### HV-9 — Call Analytics Workspace `DONE`

**Feature:** Real date-range filtering, outcome and sentiment trends over time, per-doctor and per-reason breakdowns, peak-hour analysis, AI-vs-transferred ratio, average handle time.

**Problem it solves:** `/dashboard/stats` returns four numbers, two of which are unreliable: `resolution_rate_pct` counts **any** non-"Transferred to Nurse" outcome as resolved (including `failed` and `no_answer`), and `staff_time_saved_hrs` is a hardcoded five-minutes-per-call constant presented as a measurement. `/dashboard/call-volume` is a fixed 24-hour bucket with no range selection. There is no way to answer "are we busier on Mondays?" or "which doctor gets the most reschedules?"

**[SUPERSEDED]** Shipped as `app/analytics_service.py` + `app/routers/analytics.py` (overview, daily, hourly, weekday, breakdowns, per-doctor) and `pages/app/Analytics.jsx` with a 7/30/90-day range. `tests/test_hv9_analytics.py` covers it.

Both misleading dashboard figures are fixed rather than merely supplemented: `resolution_rate_pct` now excludes `failed`/`no_answer` from its denominator (a day of entirely failed calls used to report a healthy resolution rate), and `staff_time_saved_hrs` no longer multiplies the call count by a hardcoded five minutes. Every bucket is computed in the **clinic's** timezone — `occurred_at` is UTC, so a 9 AM local rush at a UTC+5:30 clinic showed up at 03:30 on a UTC axis. No new index was needed: `ix_call_logs_org_occurred` from migration `0005` already serves the range scan. Charts are hand-rolled divs, matching the existing dashboard — a charting library is ~100 KB of bundle for four bar charts.

One rendering rule to keep: `resolution_rate_pct` is **null** when nothing connected in the range, which is not 0%. It renders as an em dash, because showing a zero tells a clinic that had a quiet week that its AI failed every call.

**How it fits the existing system:** All the raw data is already in `CallLog` (`occurred_at`, `duration_seconds`, `outcome`, `sentiment`, `direction`, `status`, `reason`). This is aggregation work, not collection work.

**User & business value:** Analytics is how you prove the product's value at renewal. It's also the natural upsell tier.

**UI location:** New `/app/analytics`, plus enriching the existing dashboard.

**Frontend impact:** Date range picker, chart components (no chart library is currently installed — the dashboard hand-rolls bars), several views. ~600 LOC.

**Backend impact:** Aggregation endpoints with `GROUP BY` and date bucketing. **Fix the resolution-rate definition** — it's currently misleading. ~350 LOC.

**Database impact:** No new tables, but **this will not perform without indexes** on `(org_id, occurred_at)`, `(org_id, outcome)`, `(org_id, status)`. Note that 104 `Column(String)` declarations lack a length and become `VARCHAR(max)` in SQL Server, which cannot be indexed — several of those columns are exactly the ones you'd filter on.

**AI/LLM/Voice impact:** Far more valuable after AI-2 (real summaries) and AI-6 (topic extraction) give you dimensions worth grouping by.

**Dependencies:** Schema/index cleanup from Phase 1.

**Difficulty:** Medium | **Priority:** Medium-High

---

### HV-10 — Per-Patient Insurance & Documents `WON'T DO — out of scope`

**[SUPERSEDED — DESCOPED]** Insurance is out of scope: this product does not handle it. Migration `0034` dropped every insurance column, and the "Insurance & Docs" tab was removed whole (per-patient documents were never built, so nothing remained). If documents are wanted later, they are a new item, not this one.


**Feature:** Store a patient's insurance carrier, member ID, group, and coverage notes, plus uploaded documents.

**Problem it solves:** The Patient Profile has an "Insurance & Docs" tab that renders a permanent empty state saying the feature doesn't exist yet — which is honest, but it's a visible hole. More importantly, `ClinicInfo.insurance_info` holds *which insurers the clinic accepts*, so the agent can answer "do you take Aetna?" but can never answer "am **I** covered?" — the question patients actually ask.

**How it fits the existing system:** Extends `Patient`. The KB upload path in `knowledge_base.py` shows the file-handling pattern (and its problems — see below).

**User & business value:** Insurance is the top reason a booked appointment fails at the front desk. Also the prerequisite for eligibility verification (F-3).

**UI location:** The existing "Insurance & Docs" tab on `/app/patients/:id`.

**Frontend impact:** Insurance form, document upload/list. ~300 LOC.

**Backend impact:** Patient insurance CRUD; document upload. **Do not copy the KB upload pattern as-is** — it reads the entire file into memory *before* checking `max_upload_bytes`, has no MIME allowlist, and writes to local disk (which breaks on any multi-instance deploy). Patient documents are PHI and need object storage with encryption at rest. ~250 LOC.

**Database impact:** Add insurance fields to `Patient` (or a separate `PatientInsurance` table if you need history). New `PatientDocument` table.

**AI/LLM/Voice impact:** Add an insurance lookup to the agent's `clinic_action` — **`REQUIRES YOUR APPROVAL`**. Note this raises the stakes on the identity-verification gap flagged in Part 1: `patient_lookup` currently returns full records on a name match with no DOB challenge.

**Dependencies:** Object storage. AI-7 (identity verification) should land before the agent can read insurance data aloud.

**Difficulty:** Medium | **Priority:** Medium

---

## A3. Advanced AI Features

Where the product's actual differentiation lives. **Everything in this section that modifies `voice-agent/` is flagged and should not be implemented without your explicit approval.**

---

### AI-1 — Multi-Tenant Voice Agent Runtime `DONE`

**Feature:** One agent deployment serving many clinics, resolving the tenant per call from the inbound SIP trunk / dialled number / room metadata rather than from a static env var.

**Problem it solves:** `backend_client._get_client()` builds a single `httpx.AsyncClient` with one static `config.BACKEND_API_KEY`. **One agent process serves exactly one clinic.** Every multi-tenant capability above it — plans, subscriptions, per-org settings, super-admin oversight — is architecturally unreachable at the voice layer. Onboarding a second clinic means deploying a second worker with its own `.env`. This is the ceiling on the entire business model.

**[SUPERSEDED]** Shipped. `backend_client` is now a per-call `TenantClient` with no module-global state; `resolve_tenant()` maps the **dialled** number (not caller ID) through the new `phone_numbers` table and returns an org-scoped `typ="agent"` token. The recommendation in this item's *AI/LLM/Voice impact* section — "prototype alongside the current single-tenant path rather than replacing it" — was followed: the whole path is gated on `BACKEND_PLATFORM_KEY`, and unsetting it restores pre-Phase-2 behaviour exactly. Every failure mode (unknown number, resolution disabled, backend unreachable) falls back to the single-tenant key rather than dropping the call. Runbook: `MULTI-TENANT-ROLLOUT.md`.

**Note on sequencing:** this shipped *before* AI-7, the evaluation harness intended to precede any agent change. See the callout in Phase 3.

**How it fits the existing system:** The backend is already correct — `get_org_context` resolves the org from the API key on every request, so it's ready for multiple tenants today. The change is confined to the agent's client construction and the tenant-resolution step at call start.

**User & business value:** Removes the hard scaling ceiling. Without it, "multi-tenant SaaS" is aspirational.

**UI location:** Phone number → org mapping on the super-admin org detail page.

**Frontend impact:** Phone number assignment UI on the admin side. ~150 LOC.

**Backend impact:** A tenant-resolution endpoint (`GET /orgs/by-phone/{number}`) authenticated by a platform-level key, plus a `PhoneNumber` model. ~150 LOC.

**Database impact:** New `PhoneNumber` table (`org_id`, `e164`, `sip_trunk_id`, `active`). Consider whether `APIKey` needs a distinct service scope.

**AI/LLM/Voice impact:** **Significant and invasive.** `backend_client` must become per-call rather than per-process (a client factory keyed by resolved org). `entrypoint` must resolve the tenant before creating the call log. Error handling for unknown numbers needs a safe fallback. I recommend prototyping this alongside the current single-tenant path rather than replacing it, and only cutting over after live validation.

**Dependencies:** None technically. This is the highest-risk item in the document — it's the one place where a mistake breaks live calls.

**Difficulty:** Hard | **Priority:** High (Critical for growth, but sequence it after Phase 1 stability)

---

### AI-2 — LLM Call Summarisation & Structured Action Extraction `DONE`

**Feature:** Replace `call_intelligence.py` with a real LLM pass over the transcript producing a clinical-grade summary, structured extracted entities (intent, symptoms mentioned, medications mentioned, requested follow-ups, unresolved questions), an accurate outcome classification, and calibrated sentiment.

**Problem it solves:** `call_intelligence.py` is word lists and f-string concatenation. `_NEGATIVE_WORDS` contains `"cancel"`, so **every appointment cancellation is scored as a "Concerned" patient**. `generate_summary` produces `"{name} called regarding {reason}. Outcome: {outcome}."` The database column is named `ai_summary` and the UI labels it "AI Summary". No LLM is involved anywhere in the analysis path.

**[SUPERSEDED — 2026-09-03]** Shipped. `app/llm_service.py` calls Groq's OpenAI-compatible endpoint with plain `httpx` (already a dependency — no SDK added for one endpoint), behind `GROQ_API_KEY`. Unset the key and the rule-based path runs exactly as before; that is the rollback switch.

The seam was as clean as this item predicted: **zero changes to `voice-agent/`**, and zero to the dashboard — the LLM overwrites `ai_summary` / `outcome` / `sentiment` in place. New columns record provenance (`summary_model`, `summary_generated_at`) plus `intent` and `extracted_json`.

Decisions worth recording:

* **Model is `openai/gpt-oss-120b`, not `llama-3.3-70b-versatile`.** The latter is not available on this account — verify against `GET /models` before changing it, because an inaccessible model returns a 404 that `llm_service` correctly refuses to retry, and every call then silently keeps the rule-based summary.
* **`outcome` is constrained to the exact vocabulary the dashboard filters on**, and an out-of-vocabulary value is *dropped* rather than coerced. A hallucinated "Appointment Made" would make the call invisible in the Call Logs filter; guessing what the model meant would be worse.
* **Rule-based values are committed first**, then upgraded in the background. A call always has a summary the instant it ends, even if the model is slow, down, or unconfigured.

**Two pre-existing bugs this surfaced, both now fixed:**

1. **`infer_outcome` returned "Transferred to Staff"** — a string nothing else in the system recognised. `routers/calls.py` escalates only on `"Transferred to Nurse"`, and so do the dashboard's outcome filter and colour map. So every real transfer — the most urgent call type there is — was notified to staff as a routine **"success"** and was invisible when they filtered for transfers. `seed.py` used the correct value, which is why it looked right in every demo.

2. **Sentiment was cruder than this item describes, in both directions.** "cancel" being in `_NEGATIVE_WORDS` is real, but the sharper failure is the other way: a single "thank you" flips a caller reporting chest pain to **"Positive"**. Both are pinned in `tests/test_ai2_llm_call_analysis.py`.

**How it fits the existing system:** The insertion point is clean and already exists: `update_call` in `routers/calls.py` calls `infer_sentiment`, `infer_outcome`, and `generate_summary` when a call reaches terminal status, and the transcript is already saved via `save_transcript_bulk`. Swapping the implementation behind those three function signatures requires **no changes to the voice agent at all** — the agent already delegates all summarisation to the backend by design. This is genuinely one of the best-placed seams in the codebase.

**User & business value:** This is the feature people believe they're buying. Real summaries make call review fast, make handoffs safe, and make the analytics in HV-9 actually meaningful.

**UI location:** `/app/calls` detail pane and patient profile — both already render `ai_summary`.

**Frontend impact:** Minimal — richer rendering of structured fields. ~150 LOC.

**Backend impact:** An LLM client (Groq is already in the stack and cheap), a prompt with strict JSON output, schema validation, graceful fallback to the existing rule-based path on failure, and a cost guard. Run it as a background task so call finalisation isn't blocked. ~350 LOC.

**Database impact:** Add `CallLog.extracted_json`, `intent`, `summary_model`, `summary_generated_at`. Keep the existing columns.

**AI/LLM/Voice impact:** **No changes to `voice-agent/`.** Backend-only. This is why it's the highest-value AI item to do first.

**Dependencies:** None. Groq key already provisioned for the agent.

**Difficulty:** Medium | **Priority:** Critical

---

### AI-3 — Real Retrieval-Augmented Knowledge Base `DONE`

**Feature:** Parse, chunk, and embed uploaded documents; crawl and index KB source URLs; vector search with a confidence threshold; answer synthesis with citations and an explicit "I don't know → offer transfer" path.

**Problem it solves:** **There is no RAG.** Uploaded documents are written to disk and immediately flipped to `"Indexed"` — the file is never opened. `/knowledge-base/search` matches on **filename only**. `KBSource` URLs are stored with status `"Indexing"` and are never crawled, so they display "Indexing" forever. `/knowledge-base/ask` is bag-of-words overlap over FAQs with threshold `best_score >= 1` — a **single shared keyword** returns that FAQ as an authoritative answer to a patient on a live call. In a healthcare context that's a clinical-safety issue, not a quality issue. And every document a clinic uploads is dead weight.

**[SUPERSEDED — 2026-09-03]** Shipped, with **no agent changes**, exactly as this item predicted. `app/embedding_service.py` + `app/rag_service.py` + `kb_chunks`.

**Embeddings run locally** (fastembed / ONNX, BAAI/bge-small-en-v1.5, 384-dim). Clinic documents and the questions patients ask of them never leave the machine — sending either to a hosted embedding API would create a new data-processor relationship to justify in a BAA. ONNX rather than sentence-transformers keeps it ~130 MB instead of ~2.5 GB of torch.

**The vector-storage decision this item flagged as "the main architectural decision":** resolved by measuring the server. It is **SQL Server 2022 Express**, which has no native `VECTOR` type — that arrived in 2025. So vectors are `VARBINARY(MAX)` and cosine runs in numpy over one org's chunks. At clinic scale that beats a round trip, and it introduces **no second datastore holding PHI-adjacent text** — which matters more here than raw speed.

**The important finding, which contradicts this item's premise.** The item frames "a confidence threshold below which the answer is 'I don't know'" as the safety mechanism. Measured against a real handbook, **that does not work**: the lowest *answerable* question scored 0.648 and the highest *unanswerable* one 0.645 — a 0.003 window. "Can I get a repeat prescription?" and "What are your COVID vaccination times?" retrieve the policy text with high similarity precisely because they are plausible clinic questions. Cosine distance cannot distinguish *related to* this document from *answered by* it.

So the threshold is only a coarse filter (0.45), and **the synthesiser is the actual safety gate** — instructed to return `INSUFFICIENT_CONTEXT` when the retrieved text does not answer the question. Verified end to end against the live model: 5/5 answerable questions answered correctly, 4/4 unanswerable declined, including all three that cleared the naive threshold. Raising the threshold would not make this safer; it would make it decline answerable questions.

**Also fixed:** documents are now genuinely parsed (PDF/DOCX/TXT/MD/CSV), chunked and embedded, with `status` reflecting reality — previously a file was flipped to "Indexed" without ever being opened, and the dashboard's "AI Index Up To Date" chip was true by assertion. Chunk size was tuned empirically: at 800 chars a handbook collapsed into one chunk whose embedding averaged every topic together and matched nothing; ~280 lifted real questions from ~0.60 to 0.63-0.82.

**Still open from this item:** `KBSource` URLs are still never crawled — they display "Indexing" forever. That needs robots.txt handling and a fetch pipeline, and is separable from document RAG.

**How it fits the existing system:** `ask_knowledge_base` already returns `{answer, matched_question, source}` and the agent already consumes it through `clinic_action("faq")`. The contract is right; the implementation behind it is empty. Replacing it requires **no agent changes**.

**User & business value:** "Upload your clinic handbook and the AI knows it" is the core promise of this category of product. Right now that promise is entirely unfulfilled. The confidence threshold is equally important — an AI that reliably says "I don't know, let me transfer you" is safer and more trusted than one that guesses.

**UI location:** `/app/knowledge-base` — real indexing status, chunk counts, failure reasons; QW-6's test console showing retrieved sources and confidence.

**Frontend impact:** Indexing progress, per-document status, citation display in the test console. ~250 LOC.

**Backend impact:** Document parsing (PDF/DOCX/TXT), chunking, embedding generation, vector storage, retrieval, and answer synthesis. Async indexing — which **requires the scheduler/queue from HV-3**. URL crawling with robots.txt respect. A confidence floor below which the answer is "I don't know". ~800 LOC.

**Database impact:** New `KBChunk` table (`org_id`, `document_id`, `chunk_index`, `text`, `embedding`, `token_count`). SQL Server 2025 has native vector support; on earlier versions you'll need an external vector store (pgvector, Qdrant, Pinecone) or a hybrid approach using SQL Server full-text search plus reranking. **This is the main architectural decision in this feature — worth deciding early.**

**AI/LLM/Voice impact:** **No changes to `voice-agent/`** — the `faq` action's response shape is unchanged. Answer quality improves immediately. Consider tightening the agent's instruction to always defer to the returned `answer` verbatim (a small prompt change, so still **`REQUIRES YOUR APPROVAL`**, though very low risk).

**Dependencies:** Embedding provider. Background job infrastructure. Vector storage decision.

**Difficulty:** Hard | **Priority:** Critical

---

### AI-4 — Per-Tenant Agent Configuration `REQUIRES YOUR APPROVAL` `OPEN`

**Feature:** Make the greeting, persona, voice, supported languages, escalation behaviour, and scope constraints actually configurable per clinic — and actually read by the agent.

**Problem it solves:** The live system prompt is a **hardcoded string literal at `agent.py:864`**. Nothing reads `AgentSettings.greeting_script` or `AgentSettings.voice_profile`. Nothing reads `RoutingRule`.

**[PARTIALLY SUPERSEDED — this item is now smaller than described]** AI-1 already delivered the hard part. `greeting_script` and `voice_profile` **are** now read: `/agent/resolve-tenant` returns both per call, the greeting is spoken, and the voice profile is matched against known TTS voice ids (with unrecognised free text safely ignored, so a clinic cannot break its own audio). What remains for AI-4: the system prompt is **still** a hardcoded literal, `RoutingRule` is **still** read by nothing (that is AI-5), and the settings surface still lacks persona, languages, escalation number, and duration fields. The per-call config-fetch mechanism this item calls for already exists — extend it rather than building it. The Agent Settings page lets a clinic pick a voice, write a greeting, and define escalation rules — **all of which have zero effect on a real call** — and then tells them "changes may require up to 5 minutes to propagate across active MediCOR instances", describing a propagation mechanism that does not exist. Separately, `config.SYSTEM_PROMPT` in the agent is a dead 12-point prompt still referencing the old tool names (`lookup_patient`, `book_appointment`) that were replaced by the single `clinic_action` router.

**How it fits the existing system:** `AgentSettings` is a per-org singleton with a `get_or_create` helper. Adding a dual-auth `GET` (so the agent can read it by API key) is trivial. The agent-side change is fetching config at `entrypoint` and composing the prompt from it.

**User & business value:** Per-tenant agent behaviour is the definition of a multi-tenant voice product. Without it every clinic gets an identical generic receptionist. It's also the fix for a currently-misleading UI.

**UI location:** `/app/agent` (page exists) — extend with language selection, persona controls, and a preview.

**Frontend impact:** Extend the existing form; remove or correct the false propagation message. ~200 LOC.

**Backend impact:** Make `GET /agent-settings` dual-auth via `get_org_context`. Extend `AgentSettings` with real fields. Add prompt-length validation, since the current prompt is deliberately tiny to control Groq TPM cost. ~120 LOC.

**Database impact:** Extend `AgentSettings` with `persona_instructions`, `languages_csv`, `tts_voice_id`, `tts_provider`, `escalation_number`, `max_call_duration_seconds`, `scope_restrictions`. Widen `RoutingRule` — see AI-5.

**AI/LLM/Voice impact:** **Direct modification of `agent.py`.** `entrypoint` must fetch settings before `AgentSession` construction; `instructions` becomes composed rather than literal; `_build_tts` must accept an org voice. **Real risks to weigh:** (a) a clinic writing a bad prompt degrades its own call quality, so you need guardrails and a hard-coded safety preamble that cannot be overridden; (b) prompt length directly drives Groq token cost and latency, and the current prompt is minimal *on purpose*; (c) a config fetch failure at call start must fall back to the current hardcoded prompt, never fail the call. I'd recommend delivering this behind a per-org feature flag with the current behaviour as the default.

**Dependencies:** AI-1 makes this dramatically more valuable (per-tenant config is nearly moot when one worker serves one clinic).

**Difficulty:** Medium-Hard | **Priority:** High

---

### AI-5 — Structured Escalation & Routing Rules `REQUIRES YOUR APPROVAL` `PARTIAL`

**[PARTIALLY SUPERSEDED — 2026-09-04]** The dead-table half of this item is closed. Enabled
`RoutingRule` rows now travel on `/agent/resolve-tenant` (`escalation_rules_for` in
`app/routers/agent_runtime.py`, capped by `MAX_ESCALATION_RULES` / `MAX_RULE_CHARS` because the
prompt is billed per call), and `agent.py` renders them into an `ESCALATION:` block in the system
prompt. A rule written on `/app/agent` therefore does change what a live call does, which is what
the "Problem it solves" paragraph below is about. `tests/test_ai5_escalation_rules.py` pins the
parts that matter: disabled rules withheld, no cross-clinic leakage, NULL `enabled` failing open
(toward transferring to a human), and the prompt-size cap.

**Still open:** everything structural — the `condition_type` / `condition_value` / `action` /
`destination_number` / `priority` columns, routing to *different* destinations per rule, the
condition-builder UI, and server-side evaluation. Matching is currently done by the LLM reading
the rule text, which handles Hindi and Telugu phrasing that a keyword list would miss, but is
**probabilistic, not a hard interlock** — do not describe it as deterministic.

**Feature:** Turn routing rules from free text into structured, evaluated conditions — escalate on detected urgency, on specific keywords, outside clinic hours, on repeated AI failure, on explicit caller request, or after N turns without progress.

**Problem it solves:** `RoutingRule` has exactly three columns: `title`, `detail`, `enabled`. There is no condition, no action, no target. **Nothing in the agent reads the table.** The UI says "Define when MediCOR should escalate calls to human staff" above a list that cannot influence anything. Meanwhile the agent's actual transfer logic is a single `destination` parameter defaulting to `config.DEFAULT_TRANSFER_NUMBER` — one number, no routing.

**How it fits the existing system:** The `transfer` action in `clinic_action` already accepts a `destination` and already handles SIP address construction. Routing to *different* destinations by condition is a natural extension of an existing parameter.

**User & business value:** "Urgent calls reach a nurse immediately, billing questions reach the front desk, after-hours reaches the on-call line" is a concrete, sellable clinical-safety story. It also makes the AI safer by defining when it must stop trying.

**UI location:** `/app/agent` — replace free-text rules with a condition builder.

**Frontend impact:** Rule builder UI (condition type, parameters, destination, priority). ~400 LOC.

**Backend impact:** Structured rule storage, validation, and a dual-auth read endpoint. Optionally evaluate rules server-side and return a decision, which keeps logic out of the agent. ~250 LOC.

**Database impact:** Add `condition_type`, `condition_value`, `action`, `destination_number`, `priority` to `RoutingRule`. Keep `title`/`detail` for display.

**AI/LLM/Voice impact:** **Modifies `agent.py`.** Rules must be fetched at call start and consulted before transfer. Lowest-risk implementation: keep evaluation server-side, so the agent asks the backend "should I transfer, and where?" rather than embedding logic. Note `max_tool_steps=1` constrains multi-step reasoning within a turn, which affects how a rule can be evaluated mid-conversation.

**Dependencies:** AI-4 (same settings surface, same fetch path).

**Difficulty:** Medium-Hard | **Priority:** Medium-High

---

### AI-6 — Caller Identity Verification `REQUIRES YOUR APPROVAL` `OPEN`

**Feature:** A verification step — DOB or another challenge — before the agent discloses or modifies patient records.

**Problem it solves:** `clinic_action("patient_lookup")` returns **full patient records** on a name-or-phone match with no identity challenge. On a common name, or from a shared household phone, this discloses the wrong person's PHI. Separately, `cancel` and `reschedule` require only an `appointment_id` and no caller-identity binding at the API layer — the prompt asks for confirmation, but the backend does not enforce it. This was flagged in Part 1 as one of two issues in the frozen layer.

**How it fits the existing system:** `search_patients` already returns a list; the natural change is returning minimal identifiers plus a `verification_required` flag, then a separate `verify` action that unlocks the full record for the call.

**User & business value:** This is a PHI disclosure risk, not a feature gap. It will surface in any security review or BAA negotiation. It's also cheap insurance against a genuinely bad incident.

**UI location:** Verification policy toggle on `/app/agent`.

**Frontend impact:** Policy toggle. ~50 LOC.

**Backend impact:** Split lookup into "match" (minimal fields) and "verify" (full record after a DOB check). Bind destructive appointment actions to a verified patient for that call. ~200 LOC.

**Database impact:** Add `CallLog.verified_patient_id` and `verified_at` to record that verification occurred — useful for the audit trail in HV-1.

**AI/LLM/Voice impact:** **Modifies `agent.py` tool behaviour and the prompt.** The conversation flow gains a step, which adds ~10–15 seconds to identified calls. Handling failed verification gracefully (offer transfer, never lock out) matters. I'd class this as a *safety* change rather than a feature, and recommend it as the **first** voice-layer change you approve, because the risk of the current behaviour is asymmetric.

**Dependencies:** None.

**Difficulty:** Medium | **Priority:** High (safety)

---

### AI-7 — Call Quality Evaluation Harness `PARTIAL`

**Feature:** Automated scoring of every call — did the AI achieve the caller's goal, did it hallucinate, did it follow policy, was it appropriately concise — plus a regression suite of recorded conversations to run against prompt changes.

**Problem it solves:** There is no way to tell whether a prompt change made the agent better or worse. `agent.py` shows a history of behavioural fixes (`_resolve_start_at` recovering from the LLM returning bare times, `min_delay` tuning for late Sarvam transcripts, `max_tool_steps=1` for cost) — all of which were presumably found in production because there is no offline evaluation. The three existing tests cover tenant isolation, a DDL detail, and a null edge case; **nothing tests the agent**.

**[PARTIALLY SUPERSEDED — 2026-09-03]** The scoring half is shipped: `app/evaluation_service.py` (LLM-as-judge), the `call_evaluations` table, and `/quality/*` (summary, review queue, per-call score, sign-off, admin-only batch run). No production agent changes, as this item promised.

**The signal that makes hallucination detectable** is `actions_taken`: the judge is shown which backend tools actually ran alongside the transcript, so "I've booked you in for Thursday" with no booking tool is caught. That is invisible from the transcript alone. Verified against the real judge on three hand-built calls — a clean booking scored 95; a claimed-but-unbooked appointment scored 30 with `hallucination_detected` and `invented_information`.

**A finding that shaped the design.** A call where the AI told a patient to take 400mg ibuprofen scored **70 with `goal_achieved=True`** — the caller did get an answer, so any "flag below 60" rule passes it. `needs_review` therefore triggers on a hallucination or a policy violation **regardless of score**, and the summary reports violation counts separately from the average: a run of concise, efficient calls that each stated one wrong fact shows a healthy average and is still the most urgent thing on the page.

Policy violations use a closed vocabulary (`medical_advice`, `unverified_disclosure`, `invented_information`, `missed_escalation`, `unprofessional_tone`) and out-of-vocabulary codes are dropped, so counts stay comparable across runs.

**[SUPERSEDED — 2026-09-04]** The UI half is shipped: `pages/app/CallQuality.jsx`, reached as a `?tab=quality` tab on `/app/calls` rather than a thirteenth sidebar entry, because quality is a way of reading calls rather than a separate part of the product. It shows the summary tiles, the violation counts, a review queue with sign-off, and the judge's reasoning per call; the "evaluate 20 recent calls" button is admin-only and never fires on mount. Two of this item's own findings are encoded in it: the queue is driven by `needs_review` and never by a score threshold, and violation counts sit *beside* the average rather than inside it.

**Still open: the replay harness.** Running a stored transcript against a *candidate* prompt needs the agent's tool loop, not just its text — a replay that cannot call `check_availability` cannot tell you whether the new prompt still books correctly. That belongs with AI-4, when prompts become editable, and is the reason AI-4 should not ship before it.

**Sequencing note:** this was meant to precede any voice-layer change. AI-1 shipped first, so this is a retrospective safety net rather than the baseline it was intended to be — there is no "before" measurement to compare against.

**How it fits the existing system:** Transcripts are already persisted in `TranscriptMessage` and actions in `CallLog.actions_taken`. That's a ready-made evaluation corpus.

**User & business value:** Lets you improve the agent safely and prove quality to customers. Prerequisite for AI-4 — you cannot let customers edit prompts without a way to detect regressions.

**UI location:** New quality tab on `/app/calls`; aggregate scores in HV-9 analytics.

**Frontend impact:** Score display, flagged-call review queue. ~300 LOC.

**Backend impact:** An LLM-as-judge scoring pass; a replay harness that runs stored transcripts against a candidate prompt; regression reporting. ~500 LOC.

**Database impact:** New `CallEvaluation` table (`call_id`, `goal_achieved`, `hallucination_detected`, `policy_violations_json`, `score`, `judge_model`, `evaluated_at`).

**AI/LLM/Voice impact:** No production agent changes — this evaluates offline. That's precisely what makes it safe and why it should precede AI-4.

**Dependencies:** AI-2 (shares LLM infrastructure).

**Difficulty:** Medium-Hard | **Priority:** Medium-High

---

### AI-8 — Call Recording & Compliance Archive `REQUIRES YOUR APPROVAL` `OPEN`

**Feature:** Record calls to object storage, link them to `CallLog`, and enforce a retention policy.

**Problem it solves:** `CallLog.recording_url` and `Interaction.audio_url` are **never written by anything**. There is no LiveKit egress configured. `CallLogs.jsx` has a Play/Pause control gated on `selected.recording_url`, so it will never render. The product presents "call logs and recordings"; recordings do not exist. Separately, `Organization.data_retention_years = 7` is displayed on the Security page with **no purge job anywhere** to honour it.

**How it fits the existing system:** The schema columns and the player UI are already there. What's missing is egress and storage.

**User & business value:** Needed for dispute resolution, staff training, and QA. In many jurisdictions call recording of patient interactions carries consent obligations — **verify local requirements before enabling** (two-party consent rules vary significantly, and India's requirements differ from the US).

**UI location:** Existing player on `/app/calls`; retention settings on the Security page.

**Frontend impact:** Minimal — the player exists. ~50 LOC.

**Backend impact:** Egress webhook receiver, signed-URL generation for playback, retention purge job (needs the HV-3 scheduler). ~300 LOC.

**Database impact:** Populate `recording_url`; add `recording_status`, `recording_duration`, `retention_expires_at`.

**AI/LLM/Voice impact:** **Touches the LiveKit layer** — enabling egress in the agent's session setup. This is a genuine risk to the working call path (egress failures can affect session stability) and is the reason it's flagged. Consent capture may also require a prompt change ("this call may be recorded").

**Dependencies:** Object storage. Scheduler. **Legal review on consent — do this before the engineering.**

**Difficulty:** Medium-Hard | **Priority:** Medium

---

## A4. Future / Innovative

Genuinely valuable, but each requires a significant architectural commitment. Not for the next two quarters.

---

### F-1 — True FHIR / SMART-on-FHIR EHR Integration `OPEN`

**Feature:** Real bidirectional integration with Epic, Cerner, and athenahealth using FHIR R4 resources and OAuth2/SMART authorisation.

**Problem it solves:** `ehr_service.py` is honest in its docstring — it does a generic `POST {base_url}/{resource_type}` with an optional bearer token. There is **no FHIR resource mapping, no OAuth2, no SMART launch, no vendor certification**. Worse, the UI shows Epic, Cerner, athenahealth, and Veradigm as named integrations with seeded status strings like "Connected — Real-time" and fabricated metrics ("Last Sync: 2 mins ago", "Data Transferred: 1.2 GB"). And `EHRIntegrationUpdate.api_key` is **accepted by the endpoint and silently discarded** — `api_credentials_json` can only be set by direct database write, and is stored in plaintext. Flipping `connected = true` sets the status to "Connected — Real-time" with no credential check and no connection test. A customer cannot configure a real integration through the product at all.

**How it fits the existing system:** `ehr_service.py`'s status model (`synced` / `not_configured` / `unavailable` / `local_only`) and its never-break-the-caller philosophy are both well-designed and worth keeping. The transport underneath needs replacing.

**User & business value:** EHR integration is the gate on selling to anything larger than a single-doctor practice. It's also a multi-quarter effort per vendor including certification programmes (Epic App Orchard / Toolbox, Cerner Code).

**UI location:** `/app/ehr` — real connection wizard, OAuth consent, live status, sync history.

**Frontend impact:** OAuth flow, field mapping, sync history, error surfacing. ~800 LOC.

**Backend impact:** FHIR client, resource mappers (Patient, Appointment, Practitioner, Slot, DocumentReference), OAuth2 token management with refresh, per-vendor quirk handling, sync reconciliation. ~2,500+ LOC.

**Database impact:** `EHRConnection` with encrypted token storage; `EHRSyncLog`; external-ID mapping tables on Patient and Appointment. **Credentials must be encrypted at rest** — the current plaintext `api_credentials_json` is not acceptable for production.

**AI/LLM/Voice impact:** The agent's `medical_history` action gets real data. Requires care about what the AI is permitted to read aloud.

**Dependencies:** Vendor sandbox access and certification. Secrets management.

**Difficulty:** Hard | **Priority:** Medium (High commercially, but far out)

---

### F-2 — Multi-Location & Clinic Group Hierarchy `OPEN`

**Feature:** An organisation containing multiple locations, each with its own doctors, hours, phone number, and agent configuration, with group-level reporting.

**Problem it solves:** `Organization` is flat. A three-site practice must create three disconnected tenants with no shared patients, no shared reporting, and no cross-site booking. Multi-site groups are the highest-value customer segment.

**How it fits the existing system:** Substantial. Every `org_id` filter across ~16 routers becomes an org-or-location decision. `Doctor`, `Appointment`, `ClinicInfo`, `AgentSettings`, and `PhoneNumber` all become location-scoped while `Patient` and billing stay org-scoped.

**User & business value:** Materially larger deal sizes; the natural enterprise tier.

**UI location:** Location switcher in `AppShell`; location management on Clinic Profile.

**Frontend impact:** Location context throughout; switcher; scoped views. ~800 LOC.

**Backend impact:** Every query touched. ~1,200 LOC.

**Database impact:** New `Location` table; `location_id` added across many tables; careful backfill migration.

**AI/LLM/Voice impact:** Tenant resolution becomes org+location — a direct extension of AI-1.

**Dependencies:** **AI-1 must land first.** Also needs Alembic migrations, which don't currently exist. **[SUPERSEDED]** Both prerequisites are now met: AI-1 shipped, and Alembic is in place with 4 revisions.

**Difficulty:** Hard | **Priority:** Low-Medium

---

### F-3 — Insurance Eligibility Verification `WON'T DO — out of scope`

**[SUPERSEDED — DESCOPED]** Insurance is out of scope: this product does not handle it. Migration `0034` dropped every insurance column, and the "Insurance & Docs" tab was removed whole (per-patient documents were never built, so nothing remained). If documents are wanted later, they are a new item, not this one.


**Feature:** Real-time coverage and copay checks against a clearinghouse during the call.

**Problem it solves:** Insurance surprises at the front desk are a leading cause of failed appointments. The agent can currently only recite which insurers the clinic accepts.

**How it fits the existing system:** Extends HV-10; new outbound integration alongside `ehr_service`.

**User & business value:** Strong differentiator; measurable revenue impact; supports upsell pricing.

**UI location:** Patient profile insurance tab; eligibility result on the appointment.

**Frontend impact:** ~250 LOC. **Backend impact:** Clearinghouse integration (X12 270/271 or a modern API), caching, error handling. ~600 LOC.

**Database impact:** `EligibilityCheck` table with results and expiry.

**AI/LLM/Voice impact:** New agent action — **`REQUIRES YOUR APPROVAL`**. Latency is the key constraint: a clearinghouse call can take several seconds, which is a long silence on a live call. Consider async-with-callback rather than in-call blocking.

**Dependencies:** HV-10, AI-6 (never quote coverage to an unverified caller). Clearinghouse contract.

**Difficulty:** Hard | **Priority:** Low-Medium

---

### F-4 — Patient Self-Service Portal `OPEN`

**Feature:** A tokenised link sent by SMS/email letting patients confirm, reschedule, cancel, and complete intake forms without calling.

**Problem it solves:** Every interaction currently requires a phone call. Many patients would rather tap a link — and each deflected call reduces voice minutes, which is your primary cost.

**How it fits the existing system:** Reuses `/appointments/availability`, `/book`, `/reschedule`, `/cancel` behind a scoped short-lived token. A third auth family alongside user and super-admin JWTs — the existing `typ`-claim separation pattern extends naturally.

**User & business value:** Lowers cost per appointment; improves patient experience; drives reminder confirmation rates.

**UI location:** New public routes outside `AppShell`.

**Frontend impact:** New mobile-first flows. ~700 LOC. **Backend impact:** Scoped tokens, strict rate limiting, public endpoints hardened against enumeration. ~400 LOC.

**Database impact:** `PatientAccessToken` table.

**AI/LLM/Voice impact:** None — this deliberately bypasses the voice layer.

**Dependencies:** HV-3 (delivery). Careful security review: these are unauthenticated-by-link endpoints touching PHI.

**Difficulty:** Hard | **Priority:** Low-Medium

---

### F-5 — Clinical Triage with Guardrails `OPEN`

**Feature:** Structured symptom intake with strict red-flag detection and immediate escalation — never diagnosis, never advice.

**Problem it solves:** The agent's prompt says "Never give medical advice", which is correct. But it also means a caller describing chest pain gets routed like a booking request. A bounded triage layer that recognises emergencies and escalates is a safety improvement, not a clinical one.

**Value:** Genuine patient-safety benefit and a strong differentiator — but also the highest-liability feature in this document.

**AI/LLM/Voice impact:** **`REQUIRES YOUR APPROVAL`** and, more importantly, **requires clinical governance**: a licensed clinician defining the protocol, a validated red-flag list, an audit trail of every triage decision, and legal sign-off. I would not build this without that governance in place, regardless of technical readiness.

**Difficulty:** Hard | **Priority:** Low (high value, high risk — treat as a deliberate strategic bet, not a backlog item)

---

# Section B — Prioritisation

Ranked on your five criteria. Scores are 1–5; **Impact on existing system** scores *inversely* (5 = lowest disruption risk), so higher totals are better.

| Rank | Item | User | Business | Feasible | Low Effort | Low Risk | Total |
|------|------|:----:|:--------:|:--------:|:----------:|:--------:|:-----:|
| 1 | **C12** Rotate leaked credentials | 5 | 5 | 5 | 5 | 5 | **25** |
| 2 | **C3** Fix orphan appointments from pending verify | 5 | 4 | 5 | 5 | 5 | **24** |
| 3 | **QW-1** Clinic Profile & Services editor | 5 | 5 | 5 | 5 | 5 | **25** |
| 4 | **QW-2** Doctor management | 5 | 5 | 5 | 4 | 5 | **24** |
| 5 | **C2** Timezone-aware scheduling | 5 | 5 | 4 | 3 | 4 | **21** |
| 6 | **QW-4** Staff booking with availability | 5 | 4 | 5 | 4 | 5 | **23** |
| 7 | **C9** Org-validate appointment references | 4 | 5 | 5 | 5 | 5 | **24** |
| 8 | **QW-9** Complete notification coverage | 5 | 4 | 5 | 5 | 5 | **24** |
| 9 | **AI-2** LLM call summarisation | 5 | 5 | 4 | 3 | 5 | **22** |
| 10 | **QW-5** Staff reschedule & cancel | 5 | 4 | 5 | 4 | 5 | **23** |
| 11 | **HV-1** PHI audit trail | 3 | 5 | 4 | 3 | 4 | **19** |
| 12 | **HV-2** Staff & invite management | 5 | 5 | 4 | 3 | 4 | **21** |
| 13 | **Perf** Bound queries; stop 10s unbounded polling | 4 | 4 | 5 | 5 | 5 | **23** |
| 14 | **QW-7** Role-aware nav & RBAC | 3 | 4 | 5 | 5 | 4 | **21** |
| 15 | **HV-3** Appointment reminders | 5 | 5 | 3 | 2 | 4 | **19** |
| 16 | **AI-3** Real RAG | 5 | 5 | 3 | 1 | 4 | **18** |
| 17 | **HV-4** Usage metering & plan enforcement | 2 | 5 | 4 | 3 | 4 | **18** |
| 18 | **AI-6** Caller identity verification | 4 | 4 | 4 | 3 | 2 | **17** |
| 19 | **AI-1** Multi-tenant agent runtime | 2 | 5 | 3 | 2 | 1 | **13** |
| 20 | **HV-5** Stripe payments | 2 | 5 | 3 | 2 | 4 | **16** |

`AI-1` scores low on this rubric precisely because it's high-risk and high-effort — but it is a **hard ceiling on the business**, so it belongs in the roadmap regardless of where the arithmetic puts it. That's a judgement call the scoring can't make for you.

---

# Section C — Roadmap

## Phase 1 — Critical Fixes & Stability

*Goal: nothing in the product lies, nothing silently corrupts data, and no credential is compromised.*

**Phase 1: 10 of 12 complete.** Item 1 (credential rotation) can only be done by the project owner in provider consoles; item 7 is partially done (indexes shipped, column bounding outstanding). Everything else is closed. Verified against the working tree on 2026-09-03.

1. `OPEN` **Rotate every credential in both `.env` files** — DB, `SECRET_KEY`, LiveKit, Deepgram, Groq, Sarvam, SIP. Purge from the archive and from version control history. Note that rotating `SECRET_KEY` invalidates all live JWTs, so plan the cutover.
   <br>→ **Still outstanding.** `SECURITY-ROTATION.md` opens with "Status: NOT DONE". Phase 0 fixed the *exposure path* (`.gitignore` files, placeholder `.env.example`s) but could not rotate the secrets themselves.
2. `DONE` Fix **C3** — `verify_pending_booking` must set `start_at`, `patient_id`, `doctor_id`, or route through `/book`.
   <br>→ Migration `0002_pending_booking_structured_fields`. Verify now refuses pre-Phase-0 rows rather than creating a label-only shell.
3. `DONE` Fix **C2** — add `Organization.timezone`; make `_validate_slot`, `get_availability`, and `dashboard/stats` timezone-aware.
   <br>→ `app/timezones.py` documents the wall-clock vs UTC storage convention. Migration `0003`. Note `tzdata` is now a hard requirement on Windows.
4. `DONE` Fix **C9** — org-validate `patient_id` / `doctor_id` in `POST /appointments`.
   <br>→ `create_appointment` calls `_get_org_patient_or_404` / `_get_org_doctor_or_404` before the ids reach the model.
5. `DONE` Fix the `X-Forwarded-For` rate-limit bypass; add per-account login throttling.
   <br>→ `settings.trusted_proxy_ips` (empty = header ignored, the safe default) plus `LOGIN_ACCOUNT_RATE_LIMIT_*`. Covered by `tests/test_phase0_hardening.py`.
6. `DONE` Bound `list_calls`, `list_patients`, `list_appointments` with default limits; remove the 10-second unbounded `listCalls()` poll from `Dashboard.jsx`; fix the N+1 CSV export.
   <br>→ **Done 2026-09-03.** `limit`/`offset` added to `list_appointments`, matching `list_calls` and `list_patients` (all capped at 500 server-side; omitting `limit` preserves prior behaviour). The 10s `setInterval` in `Dashboard.jsx` is deliberately **kept** — it is the reconciliation safety net for missed `call.ended` events that Section G endorses, and this document contradicted itself by also asking for its removal. Section G wins; the poll stays.
7. `PARTIAL` Introduce **Alembic** (it's already a dependency with no migrations directory). Add lengths to the 104 unbounded `String` columns; add indexes on `org_id` and `(org_id, occurred_at)`.
   <br>→ **Alembic done** (now 5 revisions). **Indexes done 2026-09-03**: revision `0005_org_id_indexes` adds 17 — a single-column `org_id` index on 14 tables, plus three composites for the query shapes that actually appear (`call_logs (org_id, occurred_at)`, `appointments (org_id, start_at)`, `audit_log (org_id, occurred_at)`); tables whose `org_id` is UNIQUE are skipped, since that constraint is already index-backed. `models.py` carries matching `index=True` / `__table_args__` so a fresh `create_all()` database produces identical index names and the migration is a no-op there. **Still open:** 101 bare `Column(String)` declarations remain unbounded VARCHAR(MAX).
8. `DONE` Remove the demo-data reconciliation hook from `main.py` startup. Move `seed.py` out of the app package or guard it against non-development environments.
   <br>→ **Done 2026-09-03.** `_fix_stale_demo_live_calls` now returns immediately when `settings.is_production`. Kept for dev/local, where the demo rows it corrects actually exist. `seed.py` is unchanged and remains opt-in (`python -m app.seed`).
9. `DONE` Fix upload handling: check size before reading, add a MIME allowlist.
   <br>→ **Done 2026-09-03.** Extension and content-type are now checked against an allowlist (`ALLOWED_UPLOAD_EXTENSIONS` / `ALLOWED_UPLOAD_TYPES`) **before a single byte is read**, and the body is consumed in 64 KiB chunks that abort the moment `max_upload_bytes` is crossed — so an oversized upload costs one chunk of memory instead of its full size. Empty uploads are rejected.
10. `DONE` Correct the README (it still describes SQLite/Postgres and references the dead `data.js`); add a frontend `.env.example` documenting `VITE_API_URL`.
    <br>→ **Done 2026-09-03.** The backend README's Production notes no longer describe a `DATABASE_URL`/Postgres swap that `config.py` forbids, and now cover the single-worker limit, `TRUSTED_PROXY_IPS`, and `AUTO_CREATE_SCHEMA`. The frontend README drops the dead `data.js` reference and documents the two-session auth model and oxlint. `frontend/.env.example` created, documenting `VITE_API_URL` (which also drives the WebSocket URL).
11. `PARTIAL` Remove dead code: `lib/data.js`, the `LiveCall` model and its three endpoints, `config.SYSTEM_PROMPT` in the agent, and the large commented-out blocks in `config.py` / `database.py`.
    <br>→ **Done 2026-09-03**, with one deliberate exception. `data.js` and the `LiveCall` model + endpoints were already gone; the ~100-line commented-out duplicate block in `voice-agent/config.py` has now been removed. **`config.SYSTEM_PROMPT` was NOT removed, and this instruction is wrong**: it is live at `agent.py:813` as `instructions or config.SYSTEM_PROMPT`, the fallback used whenever a tenant supplies no prompt of its own. It was presumably dead when this document was written and was wired up by AI-1. Deleting it would break the agent on those calls.
12. `DONE` Add a 404 route and a React error boundary.
    <br>→ `pages/NotFound.jsx`, `components/ErrorBoundary.jsx`, both wired in `App.jsx`.

**Exit criteria:** a second clinic could be onboarded without an engineer touching the database.
→ **Met.** Self-serve registration, doctor/service/clinic-profile management, and super-admin phone-number assignment all exist. The remaining Phase 1 items are hygiene and security debt, not onboarding blockers.

---

## Phase 2 — High-Value UI/UX & Workflow

*Goal: the dashboard becomes a tool a clinic actually runs its front desk on.*

~~QW-1 (Clinic Profile & Services) → QW-2 (Doctors) → QW-3 (Add Patient) → QW-4 (Staff booking) → QW-5 (Reschedule/Cancel) → QW-9 (Notification coverage) → QW-6 (AI test console) → HV-2 (Staff management) → QW-7 (Role-aware nav & RBAC) → QW-8 (Support inbox) → HV-6 (Persistent notifications) → HV-7 (Missed-call queue).~~

**Phase 2 is complete**, in the order given, with one loose end: QW-8's ticket inbox works, but `/support/docs` still returns titles with **no URLs**, so the Documentation list renders as unclickable buttons. Left deliberately — inventing documentation links that don't exist would replace a visible gap with a broken promise. Either publish the docs and add real hrefs, or drop the section.

**Exit criteria:** every backend capability has a UI, and no page describes behaviour that doesn't exist.
→ **Half met.** Every backend capability now has a UI. But two pages still describe behaviour that doesn't exist, and both are *other* items rather than Phase 2 gaps: `/app/agent` lets a clinic write escalation rules nothing reads (**AI-5**), and `/app/billing` shows a voice-minutes figure that never moves (**HV-4**). Phase 2 cannot close them.

---

## Phase 3 — Advanced AI & Voice

*Goal: the AI claims become true.*

Start with the two items that require **no changes to `voice-agent/`**:

1. ~~**AI-2** — LLM call summarisation and action extraction. Backend-only, clean seam, immediate visible value.~~ **DONE**
2. ~~**AI-3** — Real RAG. Backend-only; the `/ask` contract is unchanged.~~ **DONE** — and the contract was indeed unchanged.
3. **AI-7** — Evaluation harness. Offline; build this *before* touching the agent so you can measure any change. **SCORING DONE; replay harness outstanding** — and it needs the agent's tool loop, so it lands with AI-4 rather than before it.

Then, only with your approval, in this order:

4. **AI-6** — Caller identity verification (safety first, smallest surface).
5. **AI-4** — Per-tenant agent configuration, behind a feature flag with current behaviour as default.
6. **AI-5** — Structured escalation rules, evaluated server-side.
7. ~~**AI-1** — Multi-tenant agent runtime. Prototype in parallel; cut over only after live validation.~~ **DONE — and it was taken out of order, ahead of steps 1–6.**

> **This re-sequencing matters.** AI-1 was the one item in Phase 3 the document rated highest-risk, and it shipped *first* — before AI-7, the evaluation harness that exists precisely to detect regressions in the agent. So `voice-agent/agent.py` has been substantially modified with no offline way to measure whether call quality changed.
>
> Two consequences for the remaining Phase 3 work:
>
> - **AI-7 is now more valuable than its position suggests**, not less. It was meant to precede agent changes; it now has to serve as a retrospective safety net for one that already happened.
> - **AI-4 (per-tenant config) is now partially unblocked.** AI-1 already delivered the per-call fetch of `voice_profile` and `greeting_script` from `/agent/resolve-tenant`, which is the mechanism AI-4 needs. What remains is the rest of the settings surface and prompt composition — meaningfully less work than the item body describes.

**Exit criteria:** a prompt change can be evaluated before it ships, and each clinic's agent behaves like *its* receptionist.
→ **Half met.** Each clinic's agent uses its own greeting and voice (AI-1), and calls can now be *scored* after the fact (AI-7). But a prompt change still cannot be evaluated **before** it ships: that needs the replay harness, which needs the tool loop. The live system prompt also remains a hardcoded literal in `agent.py`.

**The three remaining items (AI-4, AI-5, AI-6) all modify `agent.py`.** They are the only Phase 3 work left, and none of it should proceed without the replay harness — that is the whole reason AI-7 was sequenced first.

---

## Phase 4 — SaaS & Commercial

HV-3 (Reminders — introduces the scheduler, which HV-8 and AI-8 also need) → HV-4 (Usage metering) → HV-5 (Stripe) → HV-8 (Waitlist backfill) → HV-9 (Analytics) → HV-10 (Patient insurance).

**Exit criteria:** the product can charge money, enforce what was paid for, and prove its value at renewal.

---

## Phase 5 — Production, Security & Scale

- **Redis** for rate limiting and WebSocket fan-out — this is what unblocks running more than one worker. Today the in-memory limiter and in-process connection manager mean horizontal scaling **silently breaks both**.
- Object storage for KB documents, patient documents, and recordings.
- Docker, CI, and a real test suite **running against SQL Server** — the current three tests run on SQLite in-memory while production is SQL Server-only, so the exact dialect issues that motivated every `# noqa: E712 - SQL Server BIT compatibility` comment are untestable today. **[SUPERSEDED — count only]** There are now **8** test files, not three. The substance of the point is unchanged and still important: all 8 run on in-memory SQLite, so every SQL Server dialect behaviour remains untested. There is still no `conftest.py`, no CI, and no Dockerfile.
- HV-1 audit trail completion; data retention purge job; encryption at rest for third-party credentials.
- Super-admin MFA; shorter, revocable, clearly-marked impersonation tokens mirrored into the clinic's own audit log.
- Move JWTs out of `localStorage` to httpOnly cookies; add refresh tokens and revocation; stop passing tokens in the WebSocket query string.
- APM, structured logging aggregation, health checks, alerting.

---

# Section D — Top 10 Highest-Value Improvements

**6 of these 10 have shipped.** The four that remain are, in the doc's own ranking, items 1, 4, 5, 8 and 9.

1. `OPEN` **Rotate all leaked credentials** — they're in the archive. **← still the single highest-value item, and still not done.**
2. `DONE` **QW-1 + QW-2: Clinic Profile, Services, and Doctor management** — without these no second customer can be onboarded, and the AI answers "I don't have that detail" to the most common question it receives.
3. `DONE` **C2: Timezone-aware scheduling** — silently corrupts every booking for any non-UTC clinic.
4. `OPEN` **AI-2: Real LLM call summarisation** — the flagship claim; clean seam; no voice-layer risk.
5. `OPEN` **AI-3: Real RAG** — uploaded documents are currently dead weight, and single-keyword FAQ matching is a clinical-safety risk on live calls.
6. `DONE` **C3: Fix orphan appointments** — staff believe a booking exists; it renders nowhere.
7. `DONE` **QW-4 + QW-5: Staff booking, reschedule, cancel** — today staff can double-book while the AI cannot, and cancellation from the dashboard is impossible.
8. `OPEN` **HV-1: PHI audit trail** — the product displays HIPAA posture with no access log.
9. `OPEN` **HV-3: Appointment reminders** — the clearest ROI story for a clinic, and it introduces the scheduler three other features need.
10. `DONE` **AI-1: Multi-tenant agent runtime** — the hard ceiling on the business.

**The remaining four in priority order: credential rotation (1), then AI-2 (4) and AI-3 (5) — both backend-only with no voice-layer risk — then HV-1 (8) and HV-3 (9).**

---

# Section E — Implementable Easily

Backend already exists, or the change is contained:

*Struck-through entries have since shipped. What remains is the current easy-win backlog.*

- ~~QW-1, QW-2, QW-3, QW-4, QW-5~~, **QW-6**, **QW-8**, ~~QW-9~~ (Quick Wins — mostly frontend wiring over existing endpoints)
- **QW-7** role-aware nav (~30 LOC) and RBAC dependencies (~60 LOC) — *partially done; `patients`, `agent_settings`, `knowledge_base` still unguarded*
- ~~Fixes C3, C9, the `X-Forwarded-For` bypass~~, **query bounds**, **the N+1 export**
- Dead-code removal: ~~`data.js`, `LiveCall`~~, **`config.SYSTEM_PROMPT`**, **commented config blocks**
- ~~404 route, error boundary~~, **README/env documentation**
- **HV-7** missed-call queue (read-side over existing data)
- **Add an index on `org_id`** — not in the original list, but it is a one-line-per-table migration and `org_id` is the filter on nearly every query in the codebase. Currently there is no index on it anywhere.

---

# Section F — Requires Major Architecture Change

- ~~**AI-1** Multi-tenant agent runtime — per-call client construction in the voice layer~~ — **SHIPPED.** Delivered exactly as recommended: built alongside the single-tenant path rather than replacing it, gated on `BACKEND_PLATFORM_KEY` as a rollback switch. See `MULTI-TENANT-ROLLOUT.md`.
- **AI-3** Real RAG — vector storage decision, async indexing pipeline, embedding infrastructure
- **HV-3** Reminders — **introduces a job scheduler**, which the codebase currently has none of *(still true — no Celery, APScheduler, or cron anywhere)*
- **HV-5** Stripe — external payment lifecycle, webhook reliability
- **Redis** for rate limiting and WebSocket fan-out — required before running >1 worker. *Still true, and the sharpest operational risk in this list: `rate_limit._attempts` is a module-level dict and `realtime.ConnectionManager` holds sockets in-process, so a second worker silently degrades both — per-worker rate limits, and dashboards that miss events broadcast by the other worker. No error is raised.*
- ~~**Alembic** — schema evolution currently relies on `create_all()` plus an additive-only column sync that cannot rename, drop, retype, or backfill~~ — **SHIPPED**, 4 revisions. Note the legacy path still runs by default: `AUTO_CREATE_SCHEMA=true` continues to call `create_all()` + `sync_missing_columns()` at startup. Set it to `false` once every environment is stamped.
- **F-1** FHIR/SMART — multi-quarter per vendor, plus certification
- **F-2** Multi-location — touches every `org_id` filter in the codebase
- **F-4** Patient portal — a third auth family with public PHI-adjacent endpoints. *Note the pattern it would follow now exists in three forms, not two: staff, super-admin, and the `typ="agent"` token added by AI-1.*
- Object storage migration — local-disk uploads break on any multi-instance deploy

---

# Section G — Do Not Change (Working Well)

These are the strongest parts of the codebase. I'd leave them alone.

> **[SUPERSEDED — framing only]** The voice layer is **no longer frozen**: AI-1 modified `agent.py`
> substantially (tenant resolution, per-call client construction, dialled-number extraction,
> per-org voice and greeting). Every *specific* item listed below was verified still present and
> intact on 2026-09-03 — `clinic_action`, `_finalize_call_then_close`, `_resolve_start_at`, and
> the `min_delay` tuning all survived that change. Treat this list as "still working well, still
> don't touch", not as "untouched".

**Voice / calling layer — was frozen; modified by AI-1, but all of the below verified intact:**
- The entire LiveKit / SIP / transfer / audio path in `agent.py`
- `_finalize_call_then_close` — the shutdown-callback ordering fix is correct and subtle; LiveKit runs callbacks via `asyncio.gather`, and separating these would reintroduce a real race
- `_resolve_start_at` — a pragmatic, well-documented workaround for the small model returning bare times
- The single `clinic_action` tool schema — a deliberate, correct Groq token-cost optimisation
- `turn_handling.endpointing.min_delay = 0.7` — tuned against observed Sarvam transcript latency
- `backend_client._request` retry policy — retries 5xx and connection errors, never 4xx. Exactly right.
- The architectural rule that the agent holds **no business logic**. This is the single best decision in the project and is why AI-2 and AI-3 can be done backend-only.

**Backend:**
- Dual-JWT separation via the `typ` claim (`decode_access_token` rejects typed tokens; `decode_super_admin_token` requires them) — correctly prevents token confusion in both directions. **[UPDATED]** Now a **three**-family separation: AI-1 added `typ="agent"`, accepted only by `get_org_context` and never by `get_current_user`/`require_admin`. The pattern held under extension, which is the strongest evidence it was the right design.
- `get_org_context` dual-auth — clean, and the reason the agent and dashboard can share endpoints without duplication. **[UPDATED]** Now triple-auth: `X-API-Key`, staff JWT, or org-scoped agent token.
- API key handling — SHA-256 for high-entropy keys with a documented rationale, plaintext returned once, `mask_api_key` for logs. Correct.
- `realtime.py`'s `run_coroutine_threadsafe` bridge — lets sync routers broadcast without going async. Well-reasoned.
- Centralised exception handlers and the consistent `{error: {code, message, request_id}}` shape
- `_validate_slot` and `get_availability` booking logic (aside from the timezone fix)
- `ehr_service.py`'s never-break-the-caller philosophy and its four-state status model — the transport needs replacing, the design does not
- `RequestContextMiddleware` request-ID correlation
- Production guards in `main.py`: fail-fast on default `SECRET_KEY`, docs disabled in production

**Frontend:**
- The `ws.js` reconnect-with-backoff client and the 10-second reconciliation safety net in `Dashboard.jsx` — the reasoning about missed `call.ended` events is sound
- Separate `AuthProvider` / `AdminAuthProvider` contexts with separate token keys and guards
- Consistent skeleton / empty / error state components in `ui.jsx`
- The `AppShell` → `RealtimeProvider` → body composition, and the comment explaining why `useRealtime()` must be called from a child

---

*End of Part 2.*
