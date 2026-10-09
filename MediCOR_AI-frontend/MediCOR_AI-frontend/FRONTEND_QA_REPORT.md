# MediCOR AI — Frontend Upgrade QA Report

**Phases 1–13 complete.** 2026-09-10.
**Companions:** `FRONTEND_UPGRADE_PLAN.md` · `FRONTEND_DESIGN_SYSTEM.md` ·
`FRONTEND_BACKEND_DEPENDENCIES.md`

---

## 1. Headline

| | |
|---|---|
| Backend changes | **ONE**, at the owner's explicit instruction — `billing_currency` INR→USD (§9). No other backend file was touched during Phases 1–13 |
| Database / schema changes | **NO** |
| API contract changes | **NO** |
| WebSocket contract changes | **NO** |
| Routes added / removed / renamed | **NO** — all 34 preserved |
| New runtime dependencies | **NO** — still 4 |
| TypeScript migration | **NO** — as instructed |
| Build | ✅ passing |
| Lint | ✅ 4 warnings, identical to the pre-change baseline |
| Render smoke | ✅ **61/61** (was 47/47 — 14 new primitive cases) |
| Backend functional suite | ✅ **53/53** against live server + real database |
| Contrast audit, both themes | ✅ **0 failures** across 34 measured pairs |

---

## 2. Automated results

### 2.1 Build

```
vite: API URL compiled into this bundle -> http://localhost:8000
dist/index.html                   0.43 kB │ gzip:   0.29 kB
dist/assets/index-*.css          52.27 kB │ gzip:  10.04 kB
dist/assets/index-*.js          648.56 kB │ gzip: 169.73 kB
✓ built in 620ms
```

The deploy guard in `vite.config.js` is intact and still refuses a
localhost bundle without `ALLOW_LOCAL_API_URL=true`.

**On bundle size:** no true pre-change baseline was captured — the first
build in this engagement already included the new primitives (644 kB). From
that point the bundle rose to 663 kB by Phase 10 and then *fell* to 648 kB
through Phases 11–12, as five copies of a control class, eight copies of a
table, four tab implementations and six loading treatments collapsed into
the shared layer. Net: **five new primitives and thirteen phases of work
for +4 kB.**

### 2.2 Lint — 4 warnings, unchanged from baseline

All four are the same `react/only-export-components` warning, on files that
export a provider *and* its hook:

```
src/lib/auth.jsx        src/lib/adminAuth.jsx
src/lib/realtime.jsx    src/components/toast.jsx   ← the only new one
```

Three pre-date this work. `toast.jsx` is the same shape by design, and I
deliberately avoided a *second* such warning by not re-exporting `useToast`
through `ui.jsx` — that file stays components-only so it keeps Fast Refresh.

### 2.3 Render smoke — 61/61

35 pages plus 26 primitive cases, server-rendered. Catches components that
throw on first frame, hooks used against their contract, missing providers,
and stray `[object Object]` / `undefined` / `className=` in output.

**It found real breakage repeatedly during this work** — see §5.

### 2.4 Contrast — 0 failures

34 pairs measured from the shipped tokens in both themes, thresholds 4.5:1
for text and 3:1 for control boundaries (WCAG 1.4.11).

| | Light | Dark |
|---|---:|---:|
| Body text on page | 16.23 | 17.03 |
| Secondary text on card | 9.39 | 7.52 |
| Accent link | 5.47 | 9.87 |
| Success / warning / error chips | 4.57 / 4.51 / 5.00 | 8.33 / 8.30 / 5.88 |
| **Live chip** | **5.72** *(was 4.28 ❌)* | 6.00 |
| **Form control border** | **4.29** *(was 1.23 ❌)* | **3.20** *(was 1.41 ❌)* |
| **Button border** | 4.46 | **3.41** *(was 2.67 ❌)* |

---

## 3. What was fixed

### 3.1 Accessibility defects (measured, not asserted)

| # | Defect | Found | Fixed in |
|---|---|---|---|
| 1 | Form control borders at **1.23:1** — every input, select and textarea | audit | Phase 1 |
| 2 | Live chip at **4.28:1** at 12px semibold | audit | Phase 1 |
| 3 | **Dark-mode button border at 2.67:1** — the `secondary` variant | **Phase 13** | Phase 13 |
| 4 | Admin console had **no navigation at all** below 1024px | audit | Phase 2 |
| 5 | `PatientFormModal` / `AppointmentFormModal` — no focus trap, Escape, scroll lock or focus return | Phases 4/6 | Phases 4/6 |
| 6 | Escalation-rule toggle: 16px target, **no accessible name** | Phase 11 | Phase 11 |
| 7 | Webhook event checkboxes: 16px targets | Phase 8 | Phase 8 |
| 8 | Transcript speakers distinguished by **colour and side only** | Phase 7 | Phase 7 |
| 9 | No focus move on route change (SPA announces nothing) | audit | Phase 2 |
| 10 | Charts conveyed values by shape alone | audit | Phase 3 |
| 11 | Text below the 12px floor in 4 files | Phase 13 | Phase 13 |

Defect 3 was found by the Phase 13 sweep, not the original audit — the
audit checked control borders but not the *button* border token, which is
the same rule applied to a different element.

### 3.2 Silently swallowed write errors — both closed

| Handler | What it did | Why it mattered |
|---|---|---|
| `AgentSettings.save()` | `try/finally`, no `catch` | A failed save looked identical to a successful one. **That string is what the agent reads to every caller.** |
| `Support.submit()` | `try/finally`, no `catch` | A ticket that never filed looked filed. Someone reporting a product problem would wait on a reply nobody received. |

**Codebase-wide sweep: zero `try/finally`-without-`catch` remain.**

### 3.3 Honesty defects — UI claiming what the backend cannot do

| Removed | Evidence |
|---|---|
| Play/pause control for interaction audio | `audio_url` has **no writer anywhere** in `app/`; `has_audio` is set only by `seed.py`. Calls are transcribed, never recorded |
| Green dot reading **"Live agent available"** | Nothing checked anything |
| **"Speaks English, Hindi and Telugu"** on the login page | The current stack is English-only by explicit decision; `Features.jsx` had already been corrected and this page was missed |
| Fixed **"12AM · 6AM · 12PM · 6PM"** chart axis | Buckets are indexed by *hours ago*, not clock hours. At 9 PM the leftmost bar was 10 PM **yesterday**. The endpoint already returned `current_hour` to resolve this and the page threw it away |

And one thing deliberately **not** built: the Voice AI explainer's five
status states. No WebSocket event carries the agent's per-turn state, so
they are the diagram's labels, with a genuinely observable two-state
indicator (`liveCalls`) beside them. Written up in
`FRONTEND_BACKEND_DEPENDENCIES.md` §1.

### 3.4 The design-system adoption gap

The audit's central finding: the system existed and about a third of the
app had never adopted it.

| Metric | At audit | Now |
|---|---:|---:|
| Raw `<input>` / `<select>` / `<textarea>` | **102** | **0** in app + admin |
| Raw `<table>` | **10** | **0** |
| Local `inputClass` / `selectClass` consts | **5** (+1 found later) | **0** |
| Inline tab implementations | **4** | **0** |
| Hand-rolled dialogs | **2** | **0** |
| Swallowed write errors | **2** | **0** |

Every remaining raw `<input>` in `src/` is a **native checkbox or file
input** — which should stay native, per §3.5 — or on the **marketing
pages**, deliberately out of scope.

### 3.5 A finding worth its own note

Four checkboxes carried `border-outline-variant`. Checking the computed
style before "fixing" it showed **`border-width: 0px`** — the class set a
border *colour* with no width utility, so it had never rendered anything.
Chrome was drawing its own native widget throughout.

The correct fix was not a better colour but removing the inert classes and
making the **label** the 44px target, since the 16px box never was one.

---

## 4. What was built

**Five new primitives**, no dependencies: `ToastProvider`/`useToast`,
`Tabs`/`TabPanel`, `Menu`/`MenuItem`, `Tooltip`, and an SVG chart set
(`BarChart`, `Sparkline`, `ChartEmpty`, `ChartSkeleton`). Plus
`Button size="icon"`, `SortableTH`, and three skeleton shapes.

**21st.dev**: searched; **adopted the API pattern, installed none of the
code.** Every result required `npx shadcn add` → Radix + CVA +
tailwind-merge + clsx, in a project that runs on four dependencies.

**Notable behaviour**: the toast bridge subscribes to `notification`, not
to `appointment.booked` / `call.started`. The backend broadcasts domain
events on every change but calls `notify()` **only when
`ctx.actor == "service"`** — the AI, never a staff member. Subscribing to
the domain events would have toasted people for their own actions *and*
fired twice for anything the agent did.

---

## 5. Verification method — and its limits

### 5.1 What was verified in a live browser (Phases 1–9)

Against the running FastAPI backend and seeded SQL Server, with Chrome:

- Login, session, dashboard with real data, WebSocket "Live"
- Patient list, search, sorting (`aria-sort` transitions), filters
  (Active 5 / All 7 / Archived 2), avatars
- Patient profile tabs, arrow-key navigation, `?tab=` deep links
- **Appointment availability: 24 real slots** from
  `/appointments/availability` — stopped before commit so nothing was
  written (10 appointments before and after)
- Call log: mobile detail sheet opening/closing, transcript speaker
  labels, filter widths
- Knowledge base and EHR controls, 44px event rows
- Computed styles confirming the border and token fixes actually landed

### 5.2 Phases 10–12 — swept and verified

The extension reconnected after Phase 13 and the outstanding sweep was
completed. **All three phases are now browser-verified at desktop width.**

| Surface | Result |
|---|---|
| Voice AI explainer (`/app/agent`) | Six stages render; "Ready · no calls in progress" chip correct; **no vendor names** |
| `ClinicProfile` — all three tabs | 10 controls, **all** with the 3:1 border and a wired label; `?tab=doctors` deep link intact |
| Add-doctor form (heaviest restructure) | Required asterisk, "Optional" hints, working-days toggle group at 40px, three `Field`-wrapped selects — correct |
| All 9 admin table pages | `scope="col"` on every header, every table scroll-wrapped, **no horizontal body overflow anywhere** |
| Console | **no errors** on any page |

**Two defects were found by the sweep and fixed:**

1. **`AdminUsers` inline role select** rendered at **1.23:1** despite carrying
   `border-border-control`. A leftover class string was appended after the
   primitive's, and `border-outline-variant` **won by stylesheet order** —
   silently undoing the Phase 1 fix on that one control. This is the same
   class-order trap as the `w-auto`/`w-full` bug in Phase 7. Swept the
   codebase afterwards: it was the only instance on a form primitive.
2. **`ErrorBoundary`'s reload button** used the divider token as its border.
   A bordered button is a control boundary under the same 1.4.11 rule; moved
   to `border-outline` (4.46:1 light / 3.41:1 dark). It renders on a crash,
   where clarity matters most.

### 5.2a Mobile — still not visually verified

`resize_window` reports success but `window.innerWidth` stays 1536: the
Chrome window is maximized and refuses to shrink. This was true in every
phase.

Verified **structurally** instead — every responsive utility the app relies
on is present in the compiled stylesheet (`lg:pl-64`, `lg:hidden`,
`sm:hidden`, `lg:static`, `min-h-11` and the rest, **0 missing**), and each
responsive surface carries both its desktop and its mobile branch:

| Surface | Mobile alternative | Desktop-only branch |
|---|---|---|
| Patients | ✅ | ✅ |
| Appointments | ✅ | ✅ |
| Call logs | ✅ | ✅ |
| **Admin tables** | horizontal scroll | ✗ — see below |

**Deviation from the approved plan:** §24 of the plan listed mobile **card
lists** for the 9 admin tables. They ship with `overflow-x-auto` horizontal
scrolling instead. For a 5–9 column audit log a scrollable table is more
usable than a card per row, and this is an internal console — but it is a
narrowing of agreed scope and is recorded here as one. The clinic-facing
tables (Patients, Appointments, Call logs) *do* have real mobile renderings.

**Still recommended:** open the app on a real phone, or in a non-maximized
window, at 375px.

### 5.3 Manual functional checks still outstanding

Verified paths are listed in §5.1. **Not** exercised end-to-end:
booking commit, reschedule, cancel, no-show; CSV export; KB upload;
inbound-message flows; payment/checkout; the `/appointment` patient link.

Most are unchanged presentation-wise, but "unchanged" is a claim about the
diff, not a test result.

---

## 6. Files

### 6.1 Changed

| Area | Files | Nature |
|---|---:|---|
| Tokens | 1 | `index.css` — 3 token values, 1 new token, both themes |
| Primitives | 1 | `ui.jsx` — border fix, `size="icon"`, `SortableTH`, skeletons, re-exports |
| New primitives | 5 | `toast.jsx`, `tabs.jsx`, `menu.jsx`, `chart.jsx`, `VoiceFlow.jsx` |
| New util | 1 | `lib/format.js` — `initialsOf` |
| Shells | 2 | `AppShell`, `AdminShell` |
| Clinic pages | 14 | presentation only |
| Admin pages | 10 | presentation only |
| Feature components | 2 | `PatientFormModal`, `AppointmentFormModal` |
| Smoke harness | 1 | extended to cover new primitives |

### 6.2 Deliberately untouched

- **All backend files** — not one modified.
- `lib/api.js`, `adminApi.js`, `patientApi.js` — API contract.
- `lib/ws.js` — socket protocol, auth, reconnect.
- `lib/auth.jsx`, `adminAuth.jsx`, both `ProtectedRoute`s.
- `vite.config.js` — the deploy guard.
- `App.jsx` — no route added, removed or renamed.
- **All 13 marketing files** — recently cleaned of fabricated claims
  (SOC 2, named EHR vendors, a nonexistent trial). Touching them risks
  reintroducing that for no operational gain.
- `PatientProfile`'s disabled "Call" button — still disabled, still
  explaining why.

---

## 7. Open items for you

| # | Item | Why it is yours |
|---|---|---|
| 1 | **Mobile at 375px on a real device** | §5.2a. The window here would not resize; verified structurally only |
| 2 | ~~`AdminPlans` labels its price "Monthly price ($)"~~ | **RESOLVED 2026-09-10.** `BILLING_CURRENCY` set to **USD** by the owner — see §9. The `($)` label was already correct; the setting was not |
| 3 | `/ehr/status` returns **"every 15 minutes"** | `CLAUDE.md` records that cadence as fabricated — no scheduled job performs it. It now reaches the UI from **seeded data**, so the fix is in `seed.py` |
| 4 | `CallLogs` "Call origin" shows **"Twilio" / "LiveKit / SIP"** | You chose to keep it. Noted so the decision is visible |
| 5 | Focus lands on a dialog's **close button**, not its first field | `useDialog`'s own comment states the opposite intent. Shared-primitive surgery across 8 dialogs — deliberately not done unasked |
| 6 | Live agent-state events | `FRONTEND_BACKEND_DEPENDENCIES.md` §1. **My recommendation is not to build it** |

---

## 8. Honest summary

The plan said this was a coverage problem rather than a redesign, and that
held. The design system was good; a third of the application had never
adopted it, and the gap is now closed — with four measured accessibility
failures fixed, two silent data-loss bugs closed, and four pieces of UI
removed that claimed capabilities the backend does not have.

Phases 10–12 were completed without a browser and have since been swept —
that sweep found two more contrast defects, both fixed, which is a fair
argument for why the gap was worth closing rather than reasoning past.

**One gap remains and is not closeable here:** mobile has never been seen at
a real viewport, because the browser window would not resize in any phase.
It is verified structurally (§5.2a) and the patterns are the ones already
proven on desktop, but that is evidence, not a look. And the admin tables
ship with horizontal scrolling where the plan said card lists — a deliberate
narrowing, recorded rather than glossed.


---

## 9. Post-delivery change: billing currency INR → USD

**Requested by the owner on 2026-09-10.** This is the only backend change in
the engagement, and it is a one-line config value:
`app/config.py` → `billing_currency: str = "USD"` (was `"INR"`).

### Why this was a correction rather than a repricing

`Plan.monthly_price_cents` holds **minor units**, and the live catalogue is
`9900 / 29900 / 79900`. `CLAUDE.md` line 1101 documents the real tiers as
**"$99 / $299 / $799"** — those numbers *are* dollars. Denominated in INR
they were quoting roughly an eightieth of the intended price.

**Nothing was repriced.** The stored amounts are untouched; only the
currency they are denominated in changed.

### Blast radius, checked before the change

| | |
|---|---|
| Existing payments | **0 in the database** — nothing to reinterpret |
| Historical invoices | Unaffected. `Payment.currency` is a per-row column and `routers/billing.py:225` prefers it over this setting |
| New Razorpay orders | Correct immediately — `create_order` reads this setting, and `billing.py:408/425` store the order's own currency on the payment row |
| Pricing page / checkout / invoice | Follow automatically; all three format with `Intl.NumberFormat` from the served code rather than a hardcoded symbol |
| `AdminPlans` "Monthly price ($)" | Now correct as written — no frontend change needed |

### Verified after restart

```
GET /public/plans  →  Starter / Growth / Enterprise, currency USD
backend smoke_test.py → 53/53
```

### One thing to be aware of

Razorpay accounts are provisioned per settlement currency. If the live
Razorpay account is an Indian one, it may accept only INR orders — in which
case order creation will start failing with a currency error even though the
application is now internally consistent. **Confirm the account supports USD
before taking real payments.** `RAZORPAY_MOCK_MODE` hides this, because mock
mode echoes whatever currency it is handed.
