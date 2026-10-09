# MediCOR AI — Frontend Upgrade Plan

**Status:** **DELIVERED** — Phases 1–13 complete, 2026-09-10.
Results, open items and the verification gap are in **`FRONTEND_QA_REPORT.md`**,
which supersedes this document for anything about the finished state.
**Companions:** `FRONTEND_DESIGN_SYSTEM.md` · `FRONTEND_BACKEND_DEPENDENCIES.md`

> **Two audit findings were wrong and are corrected in the QA report.**
> §8.8 claimed admin loading states render nothing — every one of those pages
> has a `{loading && …}` branch; the real defect was that the skeletons did not
> match the tables they replaced. And the audit missed the dark-mode button
> border (2.67:1), found in the Phase 13 sweep.
**Audited:** 2026-09-09, against the working tree at
`MediCOR_AI-frontend/MediCOR_AI-frontend/` (note the doubled folder name).
**Baseline captured before any change:** `oxlint` clean (3 pre-existing
`only-export-components` warnings), `npm run smoke` **47/47 passing**.

---

## 0. Read this first — what the audit actually found

The brief assumes a frontend that needs rescuing. That is not what is here.

This codebase has already been through at least one serious design-system pass.
`src/index.css` holds a real semantic token layer (surface ramp, on-surface
pair, three-level elevation, radius scale, full dark theme). `src/components/ui.jsx`
holds 20 primitives with focus traps, `aria-invalid`/`aria-describedby` wiring,
keyboard-operable table rows and a skip link. Comments throughout record *why*
decisions were made and which bug each one closed.

So this is **not** a redesign. Rebuilding it would destroy work that is better
than what a from-scratch pass would produce. What the audit found instead is a
**coverage problem**: the design system exists and is good, and roughly a third
of the application never adopted it.

That reframes the whole engagement, and it is the single most important thing to
agree on before I touch code:

| | |
|---|---|
| **Not doing** | New visual language, new palette, new type system, shadcn migration, TypeScript conversion, chart library |
| **Doing** | Finishing the system that exists, extending it where it has genuine holes, and fixing measured defects |

Two claims in the brief I need to correct up front, because they change the plan:

- **There is no TypeScript here.** 0 `.ts`/`.tsx` files, no `tsconfig.json`.
  `@types/react` is installed but unused. The brief asks me to "maintain
  TypeScript types". Converting 17.5k lines of JSX to TS is a large, risky,
  purely-architectural change that the brief also forbids ("do not rewrite
  working code simply because you prefer another architecture"). **Decided: stay
  JavaScript**, adding JSDoc `@param` typedefs to the shared primitives only.
- **There is no shadcn/ui, and no Radix.** The whole app runs on 4 runtime
  dependencies (`react`, `react-dom`, `react-router-dom`, `lucide-react`).
  Every 21st.dev component I found installs via `npx shadcn@latest add` and pulls
  in Radix + CVA + tailwind-merge + clsx. **Decided: install none of it**; the
  proven API shapes are reimplemented against the existing tokens. See §28.

---

## 1. Current frontend architecture

```
React 19.2  ·  Vite 8  ·  React Router 7.18  ·  Tailwind v4 (@tailwindcss/vite)
lucide-react icons  ·  oxlint  ·  plain JavaScript + JSX
```

- **4 runtime dependencies.** No UI kit, no CSS-in-JS, no state library, no data
  layer (`react-query` etc.), no chart library, no form library, no test runner.
- **Styling is Tailwind v4 `@theme` tokens.** Utilities like `bg-surface-lowest`
  compile to `var(--color-surface-lowest)`, which is what lets `.dark` re-theme
  the entire app by redeclaring ~25 custom properties — no `dark:` variants in
  components. This is a genuinely good architecture; preserve it.
- **Two isolated auth contexts by design.** `lib/auth.jsx` + `lib/api.js`
  (`medvoice_token`) for clinics; `lib/adminAuth.jsx` + `lib/adminApi.js`
  (`medvoice_admin_token`) for the platform console. They are expected to coexist
  in different tabs. **Never merge them.**
- **Build-time guard.** `vite.config.js` refuses to build when `VITE_API_URL`
  points at localhost, because that value is compiled into the bundle. Do not
  weaken it; `npm run smoke` is exempt via `isSsrBuild`.

**Inventory:** 35 page files · 23 components · 11 lib files · 34 routes · 17,512 LOC.

---

## 2. Existing routes (34)

| Group | Routes |
|---|---|
| **Public marketing** (4) | `/` · `/privacy` · `/terms` · `/contact` |
| **Auth** (3) | `/login` · `/accept-invite` · `/reset-password` |
| **Patient self-service** (1) | `/appointment` — *unauthenticated, token in URL, scoped to one appointment* |
| **Clinic app** (14) | `/app` · `/app/calls` · `/app/appointments` · `/app/patients` · `/app/patients/:id` · `/app/knowledge-base` · `/app/clinic` · `/app/support` · `/app/team` · `/app/analytics` · `/app/ehr` · `/app/agent` · `/app/security` · `/app/billing` |
| **Platform admin** (11) | `/admin/login` · `/admin` · `/admin/organizations` · `/admin/organizations/:id` · `/admin/users` · `/admin/plans` · `/admin/subscriptions` · `/admin/usage` · `/admin/phone-numbers` · `/admin/support` · `/admin/audit-log` |
| **Fallback** (1) | `*` → `NotFound` |

Two sub-views are addressed by query string rather than route, deliberately, so
they can be linked to: `/app/calls?tab=quality` and `/app/clinic?tab=doctors`.
The dashboard setup checklist deep-links to both. **Preserve this.**

**No new routes will be invented.**

---

## 3. Existing pages

<details>
<summary><b>Clinic app (14)</b></summary>

| Page | LOC | State |
|---|---|---|
| `Dashboard.jsx` | 330 | Good. KPIs, live-call band, recent calls, 24h volume bars |
| `Analytics.jsx` | 342 | Good. 6 endpoints, div-based bar charts |
| `CallLogs.jsx` | 523 | Good. Master/detail + transcript + Quality tab |
| `CallQuality.jsx` | 382 | Good |
| `Appointments.jsx` | 686 | Good. Day/Week/Month grid + mobile agenda |
| `Patients.jsx` | 320 | Good. Table + separate mobile card list |
| `PatientProfile.jsx` | 425 | **Weakest app page.** See §8 |
| `KnowledgeBase.jsx` | 510 | Mixed — 5 raw inputs |
| `ClinicProfile.jsx` | 791 | Mixed — 10 raw inputs, local `inputClass` |
| `AgentSettings.jsx` | 393 | Mixed — 5 raw inputs, **swallowed save error** |
| `EHRIntegration.jsx` | 465 | Good |
| `Security.jsx` | 513 | Mixed — raw table, local `inputClass` |
| `Team.jsx` | 388 | Good |
| `Billing.jsx` | 383 | Good |
| `Support.jsx` | 377 | Mixed — **swallowed submit error** |

</details>

<details>
<summary><b>Platform admin (11) — none adopted the design system's table/form layer</b></summary>

`AdminLogin` 97 · `AdminOverview` 246 · `AdminOrganizations` 159 ·
`AdminOrgDetail` 497 · `AdminUsers` 338 · `AdminPlans` 496 ·
`AdminSubscriptions` 399 · `AdminUsage` 312 · `AdminPhoneNumbers` 275 ·
`AdminSupport` 420 · `AdminAuditLog` 429

</details>

<details>
<summary><b>Public / auth / patient (10)</b></summary>

`Landing` 28 (composes 9 marketing components) · `Privacy` 157 · `Terms` 160 ·
`Contact` 186 · `Login` 303 · `AcceptInvite` 126 · `ResetPassword` 181 ·
`PatientAppointment` 414 · `NotFound` 35

</details>

---

## 4. Existing components

**Shared primitives — `src/components/ui.jsx` (619 LOC, 20 exports):**
`Card` · `SectionHeader` · `Chip` · `LiveDot` · `Button` · `LinkButton` ·
`Field` · `Input` · `Textarea` · `PasswordInput` · `Select` · `InlineAlert` ·
`Table` · `THead` · `TH` · `TBody` · `TR` · `TD` · `EmptyState` · `ErrorState` ·
`Skeleton` · `Modal` · `ConfirmDialog`

**Shells:** `AppShell.jsx` (497 — grouped nav, skip link, drawer focus trap,
notification bell, connection dot, theme toggle, patient search) ·
`AdminShell.jsx` (87 — **no mobile nav at all**, see §8)

**Feature components:** `AppointmentFormModal` 336 · `PatientFormModal` 272 ·
`PlanCheckout` 228 · `LiveCallBanner` 180 · `RefillQueue` 156 · `CallbackQueue` 146 ·
`OnboardingChecklist` 140 · `ErrorBoundary` 58 · `Logo` 38

**Marketing (9):** `Hero` · `Navbar` · `Pricing` · `Integration` · `Features` ·
`HowItWorks` · `FAQ` · `PageShell` · `CTAFooter` · `Security` · `TrustStrip`
— deliberately outside `ui.jsx`; a marketing page and a data table want
different rules. **Keep them separate.**

---

## 5. Existing API integrations

`src/lib/api.js` (475 LOC) is the single clinic-side client — every call goes
through one `request()` that attaches the bearer token, JSON-encodes unless
`FormData`, and throws a typed `ApiError` carrying `status` + `body`. It clears
the token on 401. `adminApi.js` (185) mirrors it for the platform console.
`patientApi.js` (75) serves the unauthenticated `/appointment` page.

~120 exported endpoint wrappers across: auth · dashboard · patients · doctors ·
calls · appointments · knowledge base · clinic info & services · agent settings ·
EHR · security & audit · billing & payments · public catalogue · support · team ·
onboarding · notifications · callback queue · refills · analytics · call quality.

**No page issues a raw `fetch`.** This is a strong boundary — the redesign will
not touch it.

**Contracts I must not break** (documented in `CLAUDE.md` as load-bearing):
- `resolution_rate_pct` may be `null`, meaning *nothing connected* — render an
  em dash, never `0%`.
- `/knowledge-base/ask` returns `{answer, matched_question, source, confidence}`;
  the voice agent keys on `source === "none"`.
- `provider` / `provider_call_sid` are `NULL` on every call predating migration
  `0033` — that means *this record predates the field*, not "unknown carrier".
  The existing truthiness guard must stay.

---

## 6. Existing WebSocket integration

`lib/ws.js` (154) — dependency-free `DashboardSocket` singleton. Token as a query
param (handshakes cannot carry headers), exponential reconnect 1s→15s, typed
pub/sub with a `"*"` wildcard.

`lib/realtime.jsx` (192) — `RealtimeProvider`, mounted **once** in `AppShell`.
Exposes `connected`, `notifications`, `unreadCount`, `markAllRead`,
`dismissNotification`, `liveCalls`, `subscribe`, `seedLiveCalls`.

Events consumed today: `notification` · `call.started` · `call.updated` ·
`call.ended` · `call.transcript_message` · `appointment.booked` ·
`appointment.cancelled`.

Three subtleties that must survive the redesign:

1. **`useRealtime()` must be called from a component rendered *underneath*
   `AppShell`**, because `AppShell` is what mounts the provider. This is why
   `Dashboard`/`CallLogs` split into a `…Body` inner component. Preserve the split.
2. **Dashboard reconciles every 10s** via `listCalls()` — a safety net for a
   missed `call.ended` frame, which would otherwise pin a call to "in progress"
   forever. Keep it.
3. **Notification identity is the server row id.** HV-6 persists first and
   broadcasts the stored id so the live copy and the fetched-history copy
   de-duplicate. Do not regenerate ids client-side.

**Zero WebSocket changes are planned.** Phase 2 adds a *presentation* layer
(toasts) that subscribes through the existing `subscribe()` — no new socket, no
new event names.

---

## 7. Existing reusable components — adoption is the core finding

`ui.jsx` is imported by **37 of 58** page/component files. But import ≠ adoption:
many files import `Card` and `Chip`, then hand-roll their forms and tables.

Measured across `src/` (excluding `ui.jsx` itself):

| Bypassing the primitive | Count | Concentrated in |
|---|---:|---|
| Raw `<input>` | **63** | `ClinicProfile` 10 · `AdminPlans` 9 · `PatientFormModal` 7 · `KnowledgeBase` 5 · `AgentSettings` 5 |
| Raw `<select>` | **33** | `AdminSubscriptions` 5 · `ClinicProfile` 4 · `AdminUsers` 3 · `AdminSupport` 3 · `AdminAuditLog` 3 |
| Raw `<textarea>` | **6** | scattered |
| Raw `<table>` | **10** | **9 of 10 are admin pages** + `Security` |
| Surviving `const inputClass` | **5** | `AppointmentFormModal` · `PatientFormModal` · `AdminPhoneNumbers` · `ClinicProfile` · `Security` |

That last row is notable: the `ui.jsx` header comment claims it replaced "11
local `const inputClass` re-declarations". Five are still there. The refactor was
started and not finished — which is exactly the shape of this whole engagement.

**Consequence, not cosmetics.** Every raw `<input>` is a control with no
`aria-invalid`, no `aria-describedby`, and no error styling. Every raw `<table>`
has rows that are not keyboard-operable and headers without `scope`. The
accessibility work in `ui.jsx` reaches only the pages that actually use it.

---

## 8. UI/UX problems found

Ordered by severity. Every item was verified in the source, not inferred.

### P0 — measured accessibility defects

**8.1 Form control borders fail WCAG 1.4.11 (non-text contrast).**
`CONTROL` in `ui.jsx:187` uses `border-outline-variant` = `#e2e8f0`, which is
**1.23:1** against white. A control's boundary requires **3:1**. Dark mode is
worse: `#24314a` on `#0d1424` = **1.41:1**. Every input, select and textarea in
the application is affected. *(Card borders and table dividers may stay as they
are — those are decorative, and the surface change carries the separation.)*

**8.2 The "live" chip fails AA for text.**
`--color-live` `#e11d48` on `--color-live-bg` `#fff1f2` = **4.28:1**. `Chip`
renders `text-xs font-semibold` — 12px, which is not "large text", so the
threshold is 4.5:1. This is the chip on every in-progress call.
Verified fix: `#be123c` gives **6.29:1** as text on `live-bg` *and* **6.29:1**
with white on it as a fill — one token change satisfies both uses.

**8.3 The platform admin console has no navigation below `lg`.**
`AdminShell.jsx:29` — `<aside className="hidden … lg:flex">`, and the `lg:hidden`
mobile header contains only a logo and the words "Platform Admin". No menu
button, no drawer. On a phone or a small tablet a super admin can reach a page
only by typing its URL. `AppShell` solved this properly; `AdminShell` never got it.

### P1 — silently swallowed errors

**8.4 `AgentSettings.save()` (line 110) is `try/finally` with no `catch`.**
A rejected save clears the spinner and shows nothing, so the operator believes
the greeting script saved when it did not. This one changes what the AI says to
callers. `CLAUDE.md` names this exact anti-pattern: *"A write handler needs a
`catch`, not just a `finally`."*

**8.5 `Support.submit()` (line 66) has the same shape.** A failed support ticket
looks identical to a filed one.

### P2 — consistency

**8.6 Four different tab implementations**, each visually distinct: bordered
segmented pills (`CallLogs`), underline (`PatientProfile`), and two more in
`ClinicProfile` and `AdminSupport`. No `Tabs` primitive exists.

**8.7 No toast/transient-feedback system anywhere.** Every confirmation is an
inline banner that the user must be looking at. Real-time events (`call.started`,
`appointment.booked`) update panels silently — if you are on Patients when a call
arrives, nothing tells you.

**8.8 Admin loading states render nothing.** `AdminOrganizations`, `AdminUsers`,
`AdminSubscriptions` and `AdminAuditLog` all gate on `{!loading && …}`, so the
card is blank while fetching. The app pages use `Skeleton`; admin never adopted it.

**8.9 `AdminShell` active nav is a solid navy pill** — the heavy treatment
`AppShell` deliberately moved away from (its own comment explains why: it made the
nav item the loudest thing on screen).

**8.10 No user menu.** The avatar in `AppShell` is a static `div`. Sign-out lives
at the bottom of the sidebar; on mobile it is behind the drawer. The brief asks
for a user menu and there is nothing to put it in.

### P3 — honesty defects (this codebase's own standard)

**8.11 `PatientProfile` renders a play/pause control for interaction audio.**
Verified against the backend: `Interaction.has_audio` is written **only in
`seed.py`**, and `audio_url` is **never written anywhere at all**. The existing
`it.has_audio && it.audio_url` guard means the button cannot actually appear —
but the seeded path still prints a duration ("2:14") beside an AI interaction,
which reads as "a recording exists". It does not. `CLAUDE.md` is explicit that
calls are transcribed and never recorded, and the brief says the same. The dead
`togglePlay`/`new Audio()` machinery should go with it.

**8.12 `PatientProfile` shows a green dot reading "Live agent available".**
Nothing checks anything. This is the same class as the "Online now" indicator
already removed from `Support.jsx` and the "Insurance Verified" badge removed
from the landing page.

### P4 — smaller items

- `PatientProfile` is the only page passing no `title` to `AppShell`; page
  identity rests on a back link. No breadcrumb anywhere despite 3-level depth.
- Appointments and Patients are duplicated between `PatientProfile`'s Overview
  sidebar and its Appointments tab.
- `CallLogs` master/detail stacks on mobile — tapping a row scrolls nothing into
  view, so the tap appears to do nothing.
- Transcript bubbles distinguish only two speakers. Tool/system activity
  (`actions_taken`) is rendered in a separate block, disconnected from where in
  the conversation it happened.
- Charts (`Dashboard`, `Analytics`) are div-bars with no axis labels, no
  tooltips beyond `title=`, and no keyboard or screen-reader access to values.

### Explicitly **not** problems (verified, do not "fix")

- Admin tables *do* have `overflow-x-auto` wrappers — they duplicate the pattern
  rather than lacking it.
- `PatientAppointment` has 0 breakpoints but is a `max-w-md` centred column —
  correct by construction for the phone it is opened on.
- `Patients` has no pagination because `GET /patients` returns the whole roster.
- No trend arrows on the dashboard: `/dashboard/stats` returns today only, so a
  "vs last week" would be invented.

---

## 9. Design-system proposal

**Keep the existing system. Finish it. Extend it in five places.**

| | Decision |
|---|---|
| Color | **Keep.** Retune 2 tokens for contrast (§10) |
| Typography | **Keep** Plus Jakarta Sans + Inter. Formalise the scale (§11) |
| Spacing | **Keep** Tailwind 4px base. Document the section rhythm (§12) |
| Radius | **Keep** (§13) |
| Elevation | **Keep** all three levels (§14) |
| Buttons | **Keep** 5 variants × 3 sizes. Add `icon` size (§15) |
| Inputs | **Keep**, fix border contrast, retire the 5 stragglers (§16) |
| Cards | **Keep** (§17) |
| Tables | **Keep**, add sorting + a density option (§18) |
| Modals | **Keep** — the focus trap is better than most libraries' (§19) |
| **Toast** | **NEW** — the one genuine hole (§20) |
| **Tabs** | **NEW** — replaces 4 bespoke implementations (§20) |
| **Menu** | **NEW** — user menu + row overflow actions (§20) |
| **Tooltip** | **NEW** — needed for charts and truncation (§20) |
| **Chart** | **NEW** — shared SVG primitives, no dependency (§20) |

---

## 10. Color system

The palette stays. It is a deep-teal clinical accent on a neutral surface ramp,
with a dedicated `live` hue kept distinct from `error` — because an active call
is the product working, not a fault. That distinction is good product thinking
and I am keeping it.

**Verified with a WCAG contrast script** (`scratchpad/contrast.py`):

| Pair | Ratio | |
|---|---:|---|
| `accent` `#0f766e` on white | **5.47** | AA text ✅ |
| `accent` on `accent-subtle` | 5.25 | AA text ✅ |
| white on `accent` | 5.47 | AA text ✅ |
| `on-surface` on `surface` | 16.23 | AAA ✅ |
| `on-surface-variant` on `surface` | 8.90 | AAA ✅ |
| `success` on `success-bg` | 4.57 | AA ✅ |
| `warning` on `warning-bg` | 4.51 | AA ✅ |
| `error` on `error-bg` | 5.00 | AA ✅ |
| dark `accent` `#2dd4bf` on dark surface | 10.29 | AAA ✅ |
| **`live` on `live-bg`** | **4.28** | ❌ **fails** |
| **`outline-variant` as a control border** | **1.23** | ❌ **fails 1.4.11** |

**Two changes, both token-level:**

```css
/* light */
--color-live: #be123c;          /* was #e11d48 — 4.28 → 6.29 both as text and as fill */
--color-border-control: #767a85; /* NEW — 4.46:1; form controls only, not dividers */

/* dark */
--color-border-control: #556784; /* 3.20:1 on --color-surface-lowest */
```

**Rejected: the UI/UX Pro Max palette.** The skill recommended a healthcare cyan
`#0891B2`. Measured, that is **3.68:1 on white — it fails AA for text.** The
accent already in this codebase is better than the recommendation, and its own
comment says it was chosen for exactly that reason. Keeping it.

The skill did confirm the direction — *Minimalism & Swiss Style*, "best for
enterprise apps, dashboards, SaaS platforms" — and its anti-patterns list is
"bright neon colors / motion-heavy animations / **AI purple-pink gradients**",
which matches your brief precisely. Nothing in this plan adds a gradient.

---

## 11. Typography system

Keep **Plus Jakarta Sans** (display) + **Inter** (body). Inter's tabular figures
matter for a data product; the pairing is already loaded and already correct.

*(The skill suggested Atkinson Hyperlegible for healthcare accessibility. It is a
legitimate choice, but swapping the family across 35 pages is a high-churn change
with no measured defect behind it — the current pairing passes every contrast and
sizing check. Not recommended. Say the word if you want it explored.)*

What is missing is a **named scale**. Today each page picks sizes ad hoc
(`text-2xl sm:text-[28px]`, `text-lg`, `text-base`, `text-[11px]`, `text-[10px]`).
To be documented in `FRONTEND_DESIGN_SYSTEM.md` and applied as pages are touched:

| Role | Class | Size / weight |
|---|---|---|
| Page title | `font-display text-2xl font-bold sm:text-[28px]` | 24→28 / 700 |
| Section title | `font-display text-lg font-bold` | 18 / 700 |
| Card title | `font-display text-base font-bold` | 16 / 700 |
| KPI figure | `font-display text-3xl font-extrabold tabular-nums` | 30 / 800 |
| Body | `text-sm` | 14 / 400 |
| Secondary | `text-sm text-on-surface-variant` | 14 / 400 |
| Label | `text-xs font-semibold text-on-surface-variant` | 12 / 600 |
| Table header | `text-xs font-bold uppercase tracking-wide` | 12 / 700 |
| Caption | `text-xs text-on-surface-variant` | 12 / 400 |

**Rules:** never below 12px; `tabular-nums` on every numeric column, duration,
count and currency; **16px minimum on mobile form inputs** (below that iOS
auto-zooms on focus — worth checking, several controls are `text-sm`).

---

## 12. Spacing system

Tailwind's 4px base, unchanged. What needs writing down is the rhythm, because it
currently varies page to page:

- Page gutters `px-4 sm:px-6 lg:px-8` · vertical `py-6`
- Between major sections `mt-6` (24px) · card grid gap `gap-4`/`gap-6`
- Card padding `p-5` (compact) / `p-6` (standard)
- Table cells `px-4 py-3.5` · dense variant `px-3 py-2`
- Inline control gap `gap-2` · label→control `mt-1` · control→error `mt-1.5`

Density target is **8/10** (dashboard-dense) per the skill's density dial — this
is an operational product where a receptionist scans rows, not a marketing page.

---

## 13. Border-radius system

Unchanged, already coherent: `--radius-sm .5rem` · `md .75rem` · `lg 1rem` ·
`xl 1.5rem`. Convention: controls `rounded-lg`, cards `rounded-xl`, chips/avatars
`rounded-full`.

---

## 14. Shadow / elevation system

Unchanged — three levels with a documented reason (`flat` / `card` / `raised` /
`overlay`). Rule: **cards `card`, hover `raised`, dialogs and popovers `overlay`.**
The new Toast, Menu and Tooltip primitives all use `overlay`, so nothing floats
at the same depth as the content behind it.

---

## 15. Button system

Keep 5 variants (`primary` `secondary` `outline` `ghost` `danger`) × 3 sizes,
`loading` with `aria-busy`, `focus-ring`, disabled at `opacity-60`.

Two additions:
- **`size="icon"`** — a square 40×40 target. There are ~30 hand-rolled icon
  buttons (`rounded-md p-2 …`) across the app, several below the 44px touch
  minimum. One size token fixes the set.
- **One primary action per view.** `Appointments` currently has "New Appointment"
  (accent) sitting beside an accent-filled view switcher, so two things compete.

---

## 16. Input / form system

`Field` is the right abstraction and already does the hard part (generated id →
`htmlFor` + `aria-describedby` + `aria-invalid`, `role="alert"` on the error).

Work: apply the §10 border fix; migrate the **63 raw inputs / 33 selects / 6
textareas**; delete the **5 surviving `const inputClass`**; add `inputMode`/
`autoComplete` on phone, email and OTP-ish fields; ensure ≥16px on mobile.

Validation stays where it is — this is presentation only.

---

## 17. Card system

Unchanged. `Card` + `SectionHeader` already cover it. The only change is
consistency: several pages hand-roll a card header (`flex items-center
justify-between border-b px-6 py-4`) instead of using `SectionHeader`.

---

## 18. Table system

`Table`/`THead`/`TH`/`TBody`/`TR`/`TD` are good — the overflow wrapper is baked
in, `TH` carries `scope="col"`, and `TR` makes clickable rows keyboard-operable
with `role="button"` + Enter/Space. That last one is precisely what the 10 raw
`<table>`s are missing.

Additions:
- **`SortableTH`** — click/Enter to sort, with `aria-sort="ascending|descending|none"`.
  Client-side only, over data already loaded.
- **`density="compact"`** for admin log-style tables.
- **Migrate all 10 raw tables** onto the primitive.

No virtualisation: the largest list is a clinic's patient roster, and
`AdminAuditLog` already pages server-side.

---

## 19. Modal system

**Do not touch the dialog internals.** `useDialog` implements Escape, scroll
lock, focus-in-on-open, focus-return-to-trigger, and a Tab cycle — correctly,
including capturing the return target at open rather than reading a possibly-stale
ref at cleanup. Only additions: `size` prop (`sm|md|lg`) and a mobile
bottom-sheet presentation below `sm`.

---

## 20. Toast / notification system — and the four other new primitives

### Toast (the one real hole)
Small provider + `useToast()`, appended to `ui.jsx` or a sibling `toast.jsx`.

- `role="status"` + `aria-live="polite"`, **never steals focus** (WCAG, and the
  skill's `toast-accessibility` rule).
- Auto-dismiss 4s (skill: 3–5s); errors persist until dismissed.
- Tones reuse the existing `Chip`/`InlineAlert` tone vocabulary.
- Stacks max 3, `overlay` elevation, bottom-right on desktop / top on mobile.
- **Subscribes to existing realtime events** via `useRealtime().subscribe` —
  `call.started`, `appointment.booked`, `appointment.cancelled`. No socket change.
- Inline banners stay where they are. A toast is for something that happened
  *elsewhere*; a form error belongs next to the field.

### Tabs
`<Tabs value onChange items />` with roving `role="tablist"`/`tab`/`tabpanel` and
arrow-key navigation. Replaces 4 bespoke implementations. Supports the existing
query-string pattern so `?tab=quality` deep links keep working.

### Menu (dropdown)
Click-outside + Escape + arrow keys + `aria-expanded`/`aria-haspopup`. Two uses:
the **user menu** in `AppShell` (§8.10) and per-row overflow actions.
`NotificationBell` already implements most of this inline — the primitive will be
extracted from it rather than invented.

### Tooltip
Hover **and** focus (never hover-only), Escape to dismiss, `aria-describedby`.
Needed for chart values and truncated cells.

### Chart primitives
Small shared SVG components: `BarChart`, `Sparkline`, `AxisY`, `ChartTooltip`,
`ChartEmpty`, `ChartSkeleton`.

**No charting library.** `Analytics.jsx` documents the reason — ~100KB for four
bar charts — and I agree. But the current div-bars have no axis labels, no
accessible values and no tooltips. SVG primitives fix that at roughly 150 lines
and zero dependencies. Per the skill's chart rules: legends, tooltip-on-interact,
axis labels with units, `>=3:1` data contrast, a real empty state, and a
`screen-reader-summary` — plus each chart gets a visually-hidden data table so
the numbers are not conveyed by shape alone.

---

## 21. Loading / skeleton system

`Skeleton` exists and is used well on 20 pages. Work: add skeletons to the
**6 admin pages that render nothing while loading** (§8.8), and standardise three
shapes — `SkeletonTable rows`, `SkeletonCard`, `SkeletonList` — so each page stops
hand-assembling them. Skeletons must match the real content's dimensions to avoid
layout shift.

---

## 22. Empty states

`EmptyState` (icon + title + detail + action) is already good and widely used.
Work: sweep the ~15 places using a bare `<p>No X yet.</p>` (`PatientProfile` has
four) onto the primitive, and give each one an action where a sensible next step
exists.

**No fake data, ever.** Unconfigured integrations must keep saying so — the
existing `EhrIndicator`'s four honest states (connected / unreachable /
not connected / excluded by plan) is the model.

---

## 23. Error states

`ErrorState` (+ retry) and `InlineAlert` exist. Work:
1. Fix the two swallowed errors (§8.4, §8.5).
2. Audit every `try/finally` and every `.catch(() => {})` — some are deliberate
   (non-fatal enhancements like notification history) and must stay; a *write*
   that fails silently must not.
3. Surface `ApiError.status` meaningfully: 403 → what plan/role is needed;
   413/415 → the upload limit; 409 → the duplicate-patient message, which the
   backend already words for staff.

---

## 24. Responsive strategy

Breakpoints stay Tailwind default (`sm` 640 / `md` 768 / `lg` 1024 / `xl` 1280).
Test at **375 / 768 / 1024 / 1440**.

The pattern already established in `Patients` and `Appointments` — *render a
different component below `sm`, don't shrink the table* — is the right one and
becomes the rule:

| Surface | Desktop | Mobile |
|---|---|---|
| Patients | table | card list ✅ *exists* |
| Appointments | Day/Week/Month grid | agenda list ✅ *exists* |
| Call logs | master + detail | list → **detail as a full-screen sheet** *(new)* |
| Admin tables | table | **card list** *(new — 9 tables)* |
| **Admin nav** | sidebar | **drawer** *(new — §8.3)* |
| Dialogs | centred | bottom sheet *(new)* |

Also: 44×44 minimum touch targets, `min-h-dvh` over `100vh`, and no horizontal
page scroll at 375px.

---

## 25. Accessibility strategy

Target **WCAG 2.1 AA**. The foundations are already here — skip link, focus
traps, `focus-visible` rings, `prefers-reduced-motion`, `aria-invalid`
wiring, keyboard-operable rows.

Work, in order:
1. **Contrast** — the two measured failures (§10).
2. **Coverage** — migrating raw inputs/tables *is* the accessibility fix; those
   controls have no ARIA today.
3. **Admin mobile nav** — currently unreachable by keyboard or touch below `lg`.
4. **Focus on route change** — move focus to `#main-content` on navigation.
5. **Live regions** — one polite region for toasts; the connection dot already
   has `role="status"`.
6. **Charts** — visually-hidden data tables (§20).
7. **Heading hierarchy** — several pages jump levels or have two `h1`s.
8. **Icon semantics** — decorative icons `aria-hidden`, icon-only buttons named.
   Mostly done; verify during migration.

Verification: keyboard-only pass on every redesigned page, contrast script re-run
on final tokens, dark mode checked independently.

---

## 26. Page-by-page redesign plan

Grouped by the phases in §31. **P** = presentation-only, no logic touched.

| # | Page | Change | Risk |
|---|---|---|---|
| 1 | **Global tokens** | 2 contrast fixes; new primitives | P · low |
| 2 | `AppShell` | User menu; toast host; focus-on-route-change | P · low |
| 2 | `AdminShell` | **Mobile drawer**; lighter active state; theme toggle | P · **med** |
| 3 | `Dashboard` | SVG charts; today's-appointments widget from data already fetched; skeleton polish | P · low |
| 4 | `Patients` | Sortable columns; `Field` migration; filter chips | P · low |
| 5 | `PatientProfile` | **Biggest win.** Page title; remove dead audio control (§8.11); remove fake "Live agent available" (§8.12); `Tabs`; de-duplicate appointments; `EmptyState` sweep | P · med |
| 6 | `Appointments` | One primary action; `Tabs` for view switch; sheet dialogs on mobile | P · med |
| 7 | `CallLogs` | **Mobile detail sheet**; three-way transcript speakers (patient / AI / tool activity); `Tabs` | P · med |
| 7 | `CallQuality` | Table primitive; skeletons | P · low |
| 8 | `EHRIntegration` | Keep the 4 honest states; card polish | P · low |
| 9 | `KnowledgeBase` | 5 inputs → `Field`; keep the 413/415 `writeError` banner | P · low |
| 10 | `AgentSettings` | 5 inputs → `Field`; **fix swallowed save (§8.4)**; **Voice AI explainer — full spec in §26a** | P · **med** |
| 11 | `ClinicProfile` | 10 inputs + 4 selects → `Field`; delete `inputClass`; `Tabs` | P · med |
| 11 | `Team`/`Security`/`Billing`/`Support` | Table + form migration; **fix swallowed submit (§8.5)** | P · low |
| 12 | **11 admin pages** | Tables → primitive; forms → `Field`; skeletons; mobile card lists | P · med |
| 13 | `Login`/`AcceptInvite`/`ResetPassword` | Minor polish — already strong | P · low |
| 13 | `PatientAppointment` | Touch targets on the slot grid; it is otherwise correct | P · low |
| — | **Marketing pages** | **Untouched.** See §33 | — |

**Voice AI presentation — APPROVED.** There is no "Voice AI" route and none is
invented: the explainer sits on the existing `/app/agent` page, read-only, above
the configuration controls. It names no vendor or technology, makes no language
or uptime claim, and its status indicator is driven only by data the frontend can
actually observe. **Full specification in §26a**; the backend dependency it
surfaced is written up in `FRONTEND_BACKEND_DEPENDENCIES.md` §1.

---

## 27. Component reuse strategy

1. **Reuse before extending** — 20 primitives exist; most pages need adoption,
   not new components.
2. **Extend before adding** — `size="icon"` on `Button` beats a new `IconButton`.
3. **Add only for a real hole** — 5 new primitives (§20), each replacing
   duplicated inline code or closing a measured gap.
4. **Extract, don't invent** — `Menu` comes out of `NotificationBell`'s existing
   click-outside/Escape logic.
5. **Marketing stays separate** — different rules, different job.
6. **No new runtime dependencies.** Staying at 4.

**Decision needed — TypeScript.** The brief asks for TS; the project is plain JS.
My recommendation: **stay JS, add JSDoc typedefs to `ui.jsx` only**, which gives
editor autocomplete and prop hints with zero build change and zero risk. A real TS
migration is a separate project. **Decided: stay JavaScript.**

---

## 28. 21st.dev components considered

Searched the catalogue for the primitives I propose to add. Results for
toast/notification: *Toast* (Base UI, stacked, auto-dismiss, positions),
*Toast Notification*, *Use Toast* (queue hook), *Notifications* ×2, *Alert*.

**Recommendation: adopt the patterns, install none of the code.**

Every result installs via `npx shadcn@latest add`, which requires
`components.json`, Radix, `class-variance-authority`, `tailwind-merge` and `clsx`
— none of which exist here. Installing one would mean standing up a second
component system beside `ui.jsx`, styled by a different mechanism, in a project
that deliberately runs on 4 dependencies. That contradicts both the brief's "avoid
unnecessary dependencies" and its "do not rewrite working code".

What I *am* taking from them is the **API shape**, which is well-proven: a
provider + `useToast()` hook, a global queue with stacking, per-toast
auto-dismiss timeouts, an optional action button, status icons per tone, and
configurable position. Implemented against the existing tokens that is ~120 lines
and themes correctly in dark mode for free.

I will re-check 21st.dev before building `Tabs` and `Menu`, and report the same
way: pattern taken, code not installed, reason.

---

## 29. Figma references

Figma MCP is connected. My honest read: **this engagement does not need a Figma
artifact**, and the brief warns against meaningless ones. The design system
already exists in code, in CSS custom properties, which is a more reliable source
of truth than a parallel Figma file that will drift — a failure mode `CLAUDE.md`
documents twice on the backend side.

Where Figma genuinely earns its place, if you want it:
- **A token/component sheet generated *from* `index.css` and `ui.jsx`** — useful
  as a shared reference for a designer or a stakeholder review, and honest
  because it is derived, not authored.
- **The `AdminShell` mobile drawer** — a real layout decision worth seeing before
  it is built.

Direction reference (inspiration only, nothing copied): Linear's information
density and restraint, Stripe's form and table clarity, Vercel's neutral surface
ramp. The skill independently resolved this product to *Minimalism & Swiss Style*.

**Decided: use Figma selectively** — visual exploration, design-system reference,
and specific complex layouts only. The application is not recreated in Figma.

---

## 30. Framer concepts

Same reasoning, more strongly. The "Unreal Methods" Framer project is available
for visual experimentation, and the brief is explicit that Framer is **not** the
production app.

Given that the production shell already exists and works, I would use Framer only
for a **motion study** — toast enter/exit, mobile sheet transition, drawer slide
— to agree timings before implementing them in React. That is genuinely easier to
judge animated than described.

**Decided: use Framer for interaction experiments only** — toast timing,
sheet/drawer behaviour, the admin mobile navigation, and other meaningful
micro-interactions. Findings are carried back into the React implementation as
timing and easing values. **The production application is not migrated to
Framer and never renders from it.**

---

## 31. Implementation order

Follows the brief's phasing, resequenced so the highest-severity defects land
first and every phase is independently shippable.

| Phase | Scope | Includes |
|---|---|---|
| **1** | Design system | Contrast fixes (§10) · `Toast` `Tabs` `Menu` `Tooltip` · `Button size="icon"` · `SortableTH` · skeleton shapes · **`FRONTEND_DESIGN_SYSTEM.md`** |
| **2** | App shell | User menu · toast host · focus-on-route-change · **`AdminShell` mobile drawer** |
| **3** | Dashboard | SVG charts · today's appointments · skeletons |
| **4** | Patients | Sorting · filters · form migration |
| **5** | Patient Profile | Dead audio + fake status removal · `Tabs` · de-duplication |
| **6** | Appointments | Primary-action hierarchy · mobile sheets |
| **7** | Call Logs + Detail | Mobile detail sheet · three-way transcript |
| **8** | EHR | Polish; keep the honest states |
| **9** | Knowledge Base | Form migration |
| **10** | Agent Settings / Voice AI | **Swallowed-error fix** · **Voice AI explainer (§26a)** |
| **11** | Settings / Profile / Team / Security / Billing / Support | Form + table migration · **swallowed-error fix** |
| **12** | **Admin console (11 pages)** | Tables · forms · skeletons · mobile cards |
| **13** | Responsive + a11y + cleanup | 375/768/1024/1440 pass · keyboard pass · contrast re-run · **`FRONTEND_QA_REPORT.md`** |

Phase 12 is large and low-risk (the admin console is internal). If time is short
it is the right thing to defer — say so and I will reorder.

**Before each phase** I will tell you: what changes, which files, what
functionality is preserved, whether any backend change is needed, and what you
should see. **After each phase:** `npm run build`, `npm run lint`, `npm run smoke`,
all green before continuing.

---

## 32. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Migrating a form breaks a handler | med | `Field` accepts an element child *or* a render prop; existing `value`/`onChange` pass through untouched. One page per commit |
| `useRealtime()` moved above `RealtimeProvider` | low | Documented in §6; the `…Body` split must survive. Smoke catches the thrown error |
| Toast subscriptions leak on unmount | med | `subscribe()` returns an unsubscribe fn; every effect returns it |
| Query-string tabs regress deep links | med | `Tabs` keeps the `useSearchParams` pattern; `?tab=quality` and `?tab=doctors` explicitly re-tested |
| Dark mode drifts | med | Every new token defined in both blocks; contrast script re-run on both |
| Smoke misses runtime bugs | **high** | Smoke proves pages *render* (no effects, no API). It cannot prove behaviour — a manual pass against a running backend is required (§34 Q2) |
| Admin console has no automated coverage | med | 11 pages, smoke-only. Manual pass before shipping phase 12 |
| Scope creep into the backend | low | Hard rule (§33). Anything requiring it stops and gets documented |

**Known limitation, stated rather than discovered:** `npm run smoke` renders
every page server-side with effects disabled. A green smoke run is *not* evidence
that data loading, WebSocket updates, or form submission still work. Only a
manual pass against a running API can show that.

---

## 33. Files that must NOT be changed

**Absolute — no edits without your explicit approval:**

| Path | Why |
|---|---|
| `MediCOR_AI-backend/**` | Backend. Out of scope entirely |
| `src/lib/api.js`, `adminApi.js`, `patientApi.js` | API contract |
| `src/lib/ws.js` | Socket protocol, auth, reconnect |
| `src/lib/auth.jsx`, `adminAuth.jsx`, `ProtectedRoute.jsx`, `AdminProtectedRoute.jsx` | Auth + token storage |
| `vite.config.js` | Contains the `VITE_API_URL` deploy guard |
| `smoke/render-smoke.jsx` | The only automated check. Extend only to add new pages |

**Behaviour-preserving — presentation may change, logic may not:**

- `src/lib/realtime.jsx` — provider *placement* and event handling stay as-is.
- `src/lib/date.js` — `strftime`-style formats are Windows-safe by design.
- `src/App.jsx` — no route added, removed or renamed.
- Every `handle*` / `submit*` / `load*` function on every page.
- Validation rules, plan gates, and the `?tab=` deep-link contract.

**Deliberately untouched this engagement:**

- **All 9 marketing components + `Landing`/`Privacy`/`Terms`/`Contact`.** These
  were audited recently and had fabricated claims removed (SOC 2, named EHR
  vendors, a 14-day trial, invented support statistics). They are honest now.
  Touching them risks reintroducing exactly that, for no operational gain.
- `ErrorBoundary.jsx`, `Logo.jsx` — small and correct.
- `PatientProfile`'s disabled "Init Call" button — deliberately inert with a
  title explaining why. A disabled control that explains itself is the honest
  state; do not enable it without building the thing behind it.

---

## 34. Final QA checklist

Run before sign-off; results go in `FRONTEND_QA_REPORT.md`.

**Build** — `npm run build` · `npm run lint` (no new warnings) ·
`npm run smoke` (≥47 passing) · bundle size compared to baseline.

**Functional, against a running backend** — login · session persists on reload ·
sign-out · dashboard real data · patient list + search + archive/restore ·
patient profile · appointments load · **book / reschedule / cancel / no-show** ·
call logs + transcript + CSV export · quality tab · EHR page ·
knowledge base upload + ask · agent settings save · team invite · billing ·
admin login + all 11 admin pages · `/appointment` token page.

**Real-time** — socket connects (dot reads "Live") · `call.started` appears ·
`call.ended` clears the band · notification bell increments · reconnect after a
backend restart · toasts fire without stealing focus.

**Contracts** — no API call added, removed or reshaped · no WebSocket event name
changed · no backend file modified · `resolution_rate_pct: null` renders "—" ·
NULL `provider` renders nothing · **no fake data anywhere**.

**Responsive** — 375 / 768 / 1024 / 1440 · no horizontal scroll at 375 ·
mobile nav works in **both** shells · tables become cards · dialogs fit ·
targets ≥44px.

**Accessibility** — keyboard-only pass on every page · visible focus everywhere ·
skip link · focus trapped in dialogs and returned on close · contrast script
re-run (**0 failures, light and dark**) · toasts `aria-live` and do not steal
focus · charts have a text alternative · reduced-motion honoured.

**States** — loading/empty/error verified on every list · no blank card while
loading · every write handler has a `catch` · 403/409/413/415 messages are useful.

---

## Decisions taken (2026-09-09) — APPROVED

| | Decision |
|---|---|
| **Q1 — TypeScript** | **Stay JavaScript.** No TS migration. JSDoc typedefs on `ui.jsx` primitives only |
| **Q2 — Live backend** | **Available.** Functional QA runs against the real API and seeded database |
| **Q3a — Figma** | **Use selectively** — visual exploration, design-system reference, specific complex layouts. Do not recreate the application in Figma |
| **Q3b — Framer** | **Use for interaction experiments only** — toast timing, sheet/drawer, admin mobile nav, micro-interactions. Production stays React |
| **Q4 — Phase 12** | **In scope.** All 11 admin pages |
| **Q5 — Voice AI explainer** | **YES.** Full specification in §26a |

**Standing constraints, confirmed by the owner:**

- No TypeScript migration.
- **No new dependencies.** No shadcn, no Radix. Runtime stays at 4 packages.
- No backend changes. No database or schema changes.
- No API contract changes. No WebSocket contract changes.
- No business-logic changes. Presentation layer only.
- All routes, auth contexts and business rules preserved.

---

## 26a. Voice AI explainer — specification (`/app/agent`)

Approved as the only genuinely new UI in this plan. It sits on the existing Agent
Settings page, above the configuration controls, and is **read-only**.

### The flow

Six stages, in the clinic manager's language, with no vendor or technology named:

```
Patient calls  →  Understands the request  →  Checks patient & availability
      →  Takes action  →  Updates the clinic system  →  Confirms with the patient
```

Rendered as inline SVG/CSS — horizontal on desktop, vertical on mobile. Each
stage carries one plain sentence, and every sentence describes something this
product measurably does (booking against real availability, writing to
`appointments`, sending a confirmation through the PF-01 path).

### Vendor names: what the constraint means in practice

The instruction is that no STT, TTS, LLM, telephony or infrastructure provider is
named **in the normal user-facing UI**. The explainer names none. Three existing
places were audited against the same rule:

| Location | Verdict |
|---|---|
| Code comments naming Sarvam / Deepgram / LiveKit (`CallLogs`, `PatientProfile`, `Login`, `Features`) | **Fine** — comments, never rendered |
| `Privacy.jsx` sub-processor list — Twilio, Deepgram, Sarvam AI, Groq | **Keep.** Naming sub-processors is a data-protection disclosure, and removing it would make the policy less honest, not more. Also on the do-not-touch list |
| **`CallLogs` "Call origin" block** — renders `"Twilio"` / `"LiveKit / SIP"` | **Needs your call.** See below |

The Call origin block exists so a support question that starts in the dashboard
can finish in the carrier's console; it renders only when the record actually
carries a provider. It is arguably the "technical/admin screen" the constraint
exempts. **Default: leave it as-is.** Say the word and it moves behind a "Technical
details" disclosure, or goes entirely.

### Status representation — what is honest

**A backend dependency was found here, and it is documented rather than built.**
See `FRONTEND_BACKEND_DEPENDENCIES.md` §1.

The dashboard WebSocket carries nine event types. **None carries the agent's
per-turn state** — there is no `agent.listening`, no tool-call event, and
`/agent-settings` returns only `voice_profile` and `greeting_script`. So
*Listening / Processing / Taking Action / Confirming* **cannot be observed by the
frontend today.**

Animating them from nothing would be a fabricated status display — the same
defect this plan removes in Phase 5 (`PatientProfile`'s "Live agent available"
dot, which checks nothing). So the request is split:

- **The five states become the diagram's own labels** — an explanation of what
  happens on every call, true whether or not a call is in progress. This is what
  actually helps a non-technical reader.
- **A genuine two-state indicator sits beside it**, driven by
  `useRealtime().liveCalls`, which is real, already populated and already
  reconciled every 10 s:
  - `Ready` — "No calls in progress"
  - `On a call` — "1 call in progress", linking to `/app/calls?call={id}`

If live per-turn state is wanted later, the proposed backend change is written up
in the dependencies document. My recommendation there is **not** to build it: the
gain is a status light, the cost is a network round trip inside the turn loop,
on a path where 3–5 s of dead air is already a documented hazard.

### What the explainer must never claim

- **No language claim.** No endpoint exposes which voice stack is deployed or
  what it speaks. `Features.jsx` was already corrected to "Indian English"; the
  explainer stays silent rather than guess.
- No latency, accuracy or uptime figure — nothing measures any of them.
- No "always on" / "online" claim — see the status rule above.

### Related finding (outside this scope, flagged not fixed)

`Login.jsx:64` still advertises **"Speaks English, Hindi and Telugu"**. Per
`CLAUDE.md` the current Twilio + Deepgram stack is **English only by explicit
decision**; Hindi and Telugu belong to the legacy LiveKit stack.
`Features.jsx` was corrected for exactly this and now reads "Indian English
Voice" — the correction pass missed `Login.jsx`.

*(To be clear, the `LANGUAGE_LABEL` maps in `Patients.jsx` and
`PatientProfile.jsx` are a different and legitimate thing: they display
`preferred_language` detected on past calls — reporting stored evidence, not
claiming a current capability.)*

This is a one-line copy fix on a page already in Phase 13's scope. **It is not in
the plan yet because it is a product claim, not a design decision** — confirm the
wording you want and I will apply it.

**Standing constraints, confirmed by the owner:**

- No TypeScript migration.
- **No new dependencies.** No shadcn, no Radix. Runtime stays at 4 packages.
- No backend changes. No database or schema changes.
- No API contract changes. No WebSocket contract changes.
- No business-logic changes. Presentation layer only.

**Revised QA position.** With a live backend available, §32's "known limitation"
is lifted: the functional and real-time checklists in §34 will be executed
against a running API and a seeded database rather than reasoned about. The first
action of Phase 1 is to capture a **pre-change functional baseline** — the same
checklist run before anything is edited — so that any later failure can be
attributed to a specific change instead of assumed to be pre-existing.

---

**Nothing will be implemented until you approve this plan.**
