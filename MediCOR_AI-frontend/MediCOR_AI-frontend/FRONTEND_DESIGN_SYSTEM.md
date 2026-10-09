# MediCOR AI — Frontend Design System

**Status:** **IMPLEMENTED** as of 2026-09-10 (Phases 1–13). This describes the
system as it now is, not as it was planned. **EXISTING** markers indicate what
pre-dated this work.
**Companions:** `FRONTEND_UPGRADE_PLAN.md` · `FRONTEND_QA_REPORT.md`

The system lives in two files and nowhere else:

| File | Holds |
|---|---|
| `src/index.css` | Tokens — color, type, elevation, radius, in a Tailwind v4 `@theme` block |
| `src/components/ui.jsx` | Primitives — every shared component |

**The rule that makes this work:** Tailwind v4 compiles `bg-surface-lowest` to
`var(--color-surface-lowest)`. Because every utility resolves through a custom
property, redeclaring ~25 properties under `.dark` re-themes the entire
application — **no `dark:` variant appears in any component.** A hex literal in a
component breaks this, which is why there are only two left in `src/` and no more
may be added.

---

## 1. Color

### 1.1 Principles

- **Semantic names, never literals.** `text-on-surface`, not `text-slate-900`.
- **Color is never the only signal.** Every status pairs a hue with an icon or a
  word. A chip that is only green does not say "confirmed".
- **`live` is not `error`.** An active call is the product working. They are
  different hues on purpose and must never be conflated.
- **Every pair is measured**, not eyeballed. Ratios below come from a WCAG
  luminance script, re-run against the final tokens in Phase 13.

### 1.2 Light theme (`:root`)

| Token | Value | Role |
|---|---|---|
| `--color-surface` | `#f7f9fb` | Page ground |
| `--color-surface-lowest` | `#ffffff` | Cards, inputs, menus |
| `--color-surface-low` | `#f2f4f6` | Table headers, inset panels |
| `--color-surface-container` | `#eceef0` | Hover fills, neutral chips |
| `--color-on-surface` | `#191c1e` | Primary text |
| `--color-on-surface-variant` | `#45464d` | Secondary text, labels |
| `--color-outline-variant` | `#e2e8f0` | **Dividers and card edges only** |
| `--color-outline` | `#76777d` | Secondary button borders (4.46:1) |
| **`--color-border-control`** | **`#767a85`** | **NEW — form control borders** |
| `--color-accent` | `#0f766e` | Brand, primary action, active nav |
| `--color-accent-hover` | `#115e59` | Primary hover |
| `--color-accent-subtle` | `#f0fdfa` | Accent chip/tint ground |
| `--color-accent-contrast` | `#ffffff` | Text on accent |
| `--color-success` / `-bg` | `#15803d` / `#dcfce7` | Confirmed, healthy |
| `--color-warning` / `-bg` | `#b45309` / `#fef3c7` | No-show, degraded |
| `--color-error` / `-bg` | `#ba1a1a` / `#ffdad6` | Failure, destructive |
| **`--color-live`** | **`#be123c`** | **CHANGED — call in progress** |
| `--color-live-bg` | `#fff1f2` | Live chip ground |
| `--color-logo` | `#0ea5b7` | The mark only. Identical in both themes |

### 1.3 Dark theme (`.dark`)

Redeclares the same properties. The accent is **lifted, not reused** — `#0f766e`
is unreadable on a dark ground:

| Token | Dark value |
|---|---|
| `--color-surface` | `#0a0f1a` |
| `--color-surface-lowest` | `#0d1424` |
| `--color-on-surface` | `#eef2f6` |
| `--color-on-surface-variant` | `#9aa7b8` |
| `--color-outline` | `#5a6b88` | **CHANGED in Phase 13** — was `#4b5b74`, 2.67:1 |
| `--color-outline-variant` | `#24314a` |
| **`--color-border-control`** | **`#556784`** (NEW) |
| `--color-accent` | `#2dd4bf` |
| `--color-accent-contrast` | `#06231f` |
| `--color-live` | `#fb7185` |
| `--color-error-contrast` | `#3a0f0f` — dark ink on light red; the surface inverts here |

### 1.4 Measured contrast

| Pair | Ratio | |
|---|---:|---|
| `accent` on white | 5.47 | AA ✅ |
| `accent` on `accent-subtle` | 5.25 | AA ✅ |
| white on `accent` | 5.47 | AA ✅ |
| `on-surface` on `surface` | 16.23 | AAA ✅ |
| `on-surface-variant` on `surface` | 8.90 | AAA ✅ |
| `success` on `success-bg` | 4.57 | AA ✅ |
| `warning` on `warning-bg` | 4.51 | AA ✅ |
| `error` on `error-bg` | 5.00 | AA ✅ |
| dark `accent` on dark `surface` | 10.29 | AAA ✅ |
| dark `on-surface-variant` on dark `surface` | 7.84 | AAA ✅ |
| **`live` `#be123c` on `live-bg`** | **5.72** | **AA ✅ (was 4.28 ❌)** |
| **white on `live` `#be123c`** | **6.29** | **AA ✅** |
| **`border-control` `#767a85` on white** | **4.29** | **1.4.11 ✅ (was 1.23 ❌)** |
| **dark `border-control` on `surface-lowest`** | **3.20** | **1.4.11 ✅ (was 1.41 ❌)** |
| **dark `outline` (button border) on `surface-lowest`** | **3.41** | **1.4.11 ✅ (was 2.67 ❌)** |

*The last row was found by the Phase 13 sweep, not the original audit: that
audit checked form-control borders but not the button-border token, which is
the same rule on a different element. All 34 pairs now pass in both themes —
re-run the script in `FRONTEND_QA_REPORT.md` §2.4 after any token change.*

**Why `#be123c` and not something lighter:** `--color-live` is used *both* as
text on `live-bg` (the chip) *and* as a solid fill under white text (the
notification badge, `LiveDot`). It has to clear 4.5:1 in both directions, and
`#be123c` does — **5.72:1** as text on `live-bg`, **6.29:1** with white on it as
a fill. `#d01340` would also pass, with less headroom.

**Why the divider token was not changed:** `--color-outline-variant` at 1.23:1
is fine for card edges and table rules — WCAG 1.4.11 governs boundaries needed to
*identify a control*, not decorative separation, and the surface change already
carries it. Only form controls move to `--color-border-control`. Raising the
divider token would visibly box the whole UI for no accessibility gain.

### 1.5 Rejected

The UI/UX Pro Max skill recommended healthcare cyan `#0891B2`. Measured:
**3.68:1 on white — fails AA for text.** The existing accent is better than the
recommendation and stays.

---

## 2. Typography

**Display:** Plus Jakarta Sans (600/700/800) · **Body:** Inter (400/500/600/700).
Both already loaded from Google Fonts in `index.css`. Inter's tabular figures are
the reason it stays — this is a product full of counts, durations and currency.

| Role | Class | Size / weight |
|---|---|---|
| Page title | `font-display text-2xl font-bold tracking-tight sm:text-[28px]` | 24→28 / 700 |
| Section title | `font-display text-lg font-bold` | 18 / 700 |
| Card title | `font-display text-base font-bold` | 16 / 700 |
| KPI figure | `font-display text-3xl font-extrabold tabular-nums` | 30 / 800 |
| Body | `text-sm` | 14 / 400 |
| Secondary | `text-sm text-on-surface-variant` | 14 / 400 |
| Label | `text-xs font-semibold text-on-surface-variant` | 12 / 600 |
| Table header | `text-xs font-bold uppercase tracking-wide` | 12 / 700 |
| Caption | `text-xs text-on-surface-variant` | 12 / 400 |
| Mono / IDs | `font-mono text-xs` | 12 / 400 |

**Rules**

1. **Nothing below 12px.** The existing `text-[11px]` and `text-[10px]` usages
   (notification timestamps, chart axis labels, nav group headings) move to 12px
   or become `sr-only` where they are decorative.
2. **`tabular-nums` on every aligned digit** — counts, durations, currency,
   ratios, table numerics.
3. **≥16px on mobile form controls.** Below 16px iOS auto-zooms on focus and the
   layout jumps. Several controls are currently `text-sm` (14px); they become
   `text-base sm:text-sm`.
4. **`text-wrap: balance` on headings**, `max-width` ~68ch on prose.
5. **One `h1` per page**, levels never skipped.

*Atkinson Hyperlegible was considered (the skill's healthcare recommendation) and
rejected: the current pairing passes every contrast and sizing check, so a
35-page family swap is churn without a defect behind it.*

---

## 3. Spacing

Tailwind's 4px base. The rhythm, which was previously ad hoc per page:

| Context | Value |
|---|---|
| Page gutter | `px-4 sm:px-6 lg:px-8` |
| Page vertical | `py-6` |
| Between major sections | `mt-6` (24px) |
| Card grid gap | `gap-4` (tiles) / `gap-6` (panels) |
| Card padding | `p-5` compact · `p-6` standard |
| Card header | `px-6 py-4` with `border-b` |
| Table cell | `px-4 py-3.5` · compact `px-3 py-2` |
| Inline controls | `gap-2` |
| Label → control | `mt-1` |
| Control → error | `mt-1.5` |

Density target **8/10 (dashboard-dense)**: a receptionist scans rows, and this is
not a marketing page.

---

## 4. Radius

`--radius-sm .5rem` · `--radius-md .75rem` · `--radius-lg 1rem` · `--radius-xl 1.5rem`

Controls `rounded-lg` · cards, panels, dialogs `rounded-xl` · chips, avatars,
dots `rounded-full` · skeletons `rounded-md`.

---

## 5. Elevation

Three levels, and the reason they exist is that there used to be one — every card
sat at the same depth, so a dialog read no closer than the list behind it.

| Level | Use |
|---|---|
| `flat` | Nested panels inside a card |
| `card` | Default surface |
| `raised` | Hover on an interactive card |
| `overlay` | Dialogs, menus, toasts, tooltips, drawers |

**Rule:** anything floating above the page uses `overlay`. The new `Toast`,
`Menu` and `Tooltip` all do, so nothing floats at content depth.

---

## 6. Buttons — **EXISTING**, one addition

**Variants:** `primary` (accent fill) · `secondary` (outline, `--color-outline`) ·
`outline` (subtle) · `ghost` (text only) · `danger` (error fill)
**Sizes:** `sm` · `md` · `lg` · **`icon` (NEW — square 40×40)**

Built in: `loading` renders a spinner *and* sets `aria-busy` *and* disables, so
the state is never conveyed by a spinner alone. `focus-ring` on every instance.
Disabled at `opacity-60` with `cursor-not-allowed`.

**`size="icon"` exists** because ~30 hand-rolled icon buttons
(`rounded-md p-2 …`) are scattered across the app, several below the 44px touch
minimum. One token fixes the set.

**One primary action per view.** Where a view currently has two accent-filled
controls competing (Appointments: "New Appointment" beside an accent view
switcher), the secondary one steps down.

---

## 7. Forms — **EXISTING**, contrast fix + migration

`Field` is the abstraction and it already does the hard part: one generated id
wired to `htmlFor`, `aria-describedby` and `aria-invalid`, with the error message
in `role="alert"`. Two call shapes — an element child for the simple case, a
render prop for a radio group or two controls under one label.

```jsx
<Field label="Phone" required error={errors.phone} hint="Include the country code">
  <Input value={phone} onChange={e => setPhone(e.target.value)} inputMode="tel" autoComplete="tel" />
</Field>
```

**Changes in Phase 1:**
- `CONTROL` border → `--color-border-control` (§1.4).
- Mobile font size → `text-base sm:text-sm`.

**Migration target (Phases 4–12):** 63 raw `<input>`, 33 raw `<select>`,
6 raw `<textarea>`, and the 5 surviving `const inputClass` declarations in
`AppointmentFormModal`, `PatientFormModal`, `AdminPhoneNumbers`, `ClinicProfile`
and `Security`.

Add `inputMode` / `autoComplete` on phone, email and name fields as each is
touched. **Validation logic does not change** — this is presentation only.

---

## 8. Cards — **EXISTING**

`Card` (`elevation`, `hoverable`, `as`) + `SectionHeader` (`title`, `detail`,
`action`). Several pages hand-roll a card header instead; they move to
`SectionHeader`.

**Not everything is a card.** Border, fill, radius and shadow each say "separate
object" — spend them by role. A list of rows inside one card beats fifteen
floating cards.

---

## 9. Tables — **EXISTING**, two additions

`Table` (wraps in `overflow-x-auto` — impossible to forget) · `THead` · `TH`
(`scope="col"`) · `TBody` · `TR` · `TD`.

`TR` with `onClick` gets `tabIndex`, `role="button"` and Enter/Space handling —
this is exactly what the 10 raw `<table>` implementations are missing.

**Additions:**
- **`SortableTH`** — client-side sort over already-loaded rows, with
  `aria-sort="ascending|descending|none"` and a direction caret.
- **`density="compact"`** — `px-3 py-2`, for admin log tables.

**No virtualisation.** The largest list is a clinic roster; `AdminAuditLog`
already pages server-side.

**Mobile:** below `sm`, render a card list instead of the table. This pattern
already exists in `Patients` and becomes the rule for all 10.

---

## 10. Chips & status — **EXISTING**

Tones: `success` · `warning` · `error` · `info` · `accent` · `neutral` · `live`.
`LiveDot` is a pulsing dot honouring `prefers-reduced-motion`.

**Every chip carries a word.** Colour alone never conveys status.

---

## 11. Dialogs — **EXISTING**, do not touch the internals

`Modal` and `ConfirmDialog` share `useDialog`, which implements Escape, body
scroll lock, focus moved to the first control on open, focus returned to the
trigger on close, and a Tab cycle held inside. It captures the return target *at
open* rather than reading a possibly-stale ref at cleanup — that detail is
load-bearing.

**Additions only:** `size` (`sm|md|lg`) and a bottom-sheet presentation below `sm`.

---

## 12. Toast — **NEW**

The one genuine hole. Every confirmation today is an inline banner the user must
already be looking at.

```jsx
const toast = useToast();
toast.success("Appointment booked", { description: "Tue 14 Oct, 10:00" });
toast.error("Couldn't save", { description: err.message, action: { label: "Retry", onClick: save } });
```

| Rule | Why |
|---|---|
| `role="status"`, `aria-live="polite"` | Announced without interrupting |
| **Never moves focus** | WCAG; stealing focus mid-typing is worse than silence |
| Auto-dismiss 4s; **errors persist** | An error the user missed is not handled |
| Max 3 stacked, oldest drops | A queue of nine is noise |
| `overlay` elevation | Nothing floats at content depth |
| Bottom-right desktop / top mobile | Clear of the thumb and the FAB zone |
| Pauses on hover and focus | Reading time is not a timer |

**Toast vs inline banner** — the distinction is not stylistic:

- **Toast** = something happened *elsewhere*, or is done and needs no action.
  A realtime `call.started`. A save that succeeded.
- **Inline banner** (`InlineAlert`) = something the user must act on, at the
  place they act. A form rejection. A failed export.
- **Never both** for one event.

**Realtime wiring:** the toast host subscribes via the *existing*
`useRealtime().subscribe` to `call.started`, `appointment.booked` and
`appointment.cancelled`. No new socket, no new event name, no backend change.
Every subscription returns its unsubscribe function.

---

## 13. Tabs — **NEW**

Replaces four bespoke implementations (`CallLogs` segmented pills,
`PatientProfile` underline, `ClinicProfile`, `AdminSupport`).

`role="tablist"` / `tab` / `tabpanel`, roving tabindex, Left/Right/Home/End keys,
`aria-selected`, `aria-controls`.

**Keeps the query-string contract.** `Tabs` accepts controlled `value`/`onChange`
so pages keep using `useSearchParams` — `/app/calls?tab=quality` and
`/app/clinic?tab=doctors` are deep-linked from the dashboard checklist and must
not regress.

Two visual variants, both in the system rather than per page: `segmented`
(bordered pills, for 2–3 peer views) and `underline` (for a detail page's sections).

---

## 14. Menu — **NEW**

Extracted from `NotificationBell`'s existing click-outside/Escape logic rather
than invented.

`aria-haspopup="menu"`, `aria-expanded`, `role="menu"`/`menuitem`, Up/Down/Home/
End/Escape, focus returned to the trigger on close, click-outside to dismiss.

Two uses: the **user menu** in `AppShell` (the avatar is currently a static
`div`; sign-out is buried at the bottom of the sidebar and unreachable on mobile
without opening the drawer) and per-row overflow actions.

---

## 15. Tooltip — **NEW**

Hover **and** focus — never hover-only — Escape to dismiss, `aria-describedby`.

**A tooltip may never be the only source of information.** It supplements a
visible label. Chart values also appear in the visually-hidden data table (§16).

---

## 16. Charts — **NEW primitives, no library**

`BarChart` · `Sparkline` · `AxisY` · `ChartTooltip` · `ChartEmpty` · `ChartSkeleton`

**Why no library:** `Analytics.jsx` already documents it — ~100KB of bundle for
four bar charts, in a project with 4 dependencies. That decision stands. What
does not stand is the current div-bars having no axis labels, no accessible
values and no tooltips.

| Rule | |
|---|---|
| One scale places marks, ticks and labels | Every label names a value the chart reaches |
| Axis labels carry units | "calls", not a bare number |
| Chart text uses theme tokens | Readable in both themes |
| Data marks ≥3:1 vs ground | WCAG non-text contrast |
| **A visually-hidden `<table>` per chart** | Values are never conveyed by shape alone |
| Real empty state | Not a blank axis frame |
| Skeleton while loading | Reserve the space; no layout shift |
| Entrance animation optional | Readable immediately; honours reduced-motion |

**`null` is not zero.** `resolution_rate_pct` is `null` when nothing connected,
which renders as an em dash. A `0%` there tells a clinic that had a quiet week
that its AI failed every call. This is a documented backend contract.

---

## 17. States: loading, empty, error

| State | Primitive | Rule |
|---|---|---|
| Loading | `Skeleton`, + new `SkeletonTable` / `SkeletonCard` / `SkeletonList` | Match real content dimensions. **Never render nothing** — 6 admin pages currently do |
| Empty | `EmptyState` | Icon + title + detail + action where a next step exists |
| Error | `ErrorState` (block) / `InlineAlert` (inline) | Say what failed *and* offer recovery |
| Unconfigured | `EmptyState` with honest wording | "Not configured" / "Connect integration" — **never fake data** |

**Every write handler needs a `catch`, not just a `finally`.** Two currently do
not (`AgentSettings.save`, `Support.submit`); both are fixed in their phases.

Deliberate silent catches stay — `notification history`, `EHR status on the
appointments page` — because those are non-fatal enhancements. A **write** that
fails silently is never acceptable.

Surface `ApiError.status` meaningfully: 403 → the plan or role needed ·
409 → the duplicate-patient message the backend already words · 413/415 → the
upload limit and accepted types.

---

## 18. Motion

Subtle. Healthcare software should feel reliable, not animated.

| Interaction | Duration | Easing |
|---|---|---|
| Hover / color | 150ms | `ease-out` |
| Dropdown, tooltip | 150ms in / 100ms out | `ease-out` |
| Toast | 200ms in / 150ms out | `ease-out` |
| Dialog, drawer, sheet | 200ms | `ease-out` |
| Skeleton pulse | existing | — |

**Rules:** `transform` and `opacity` only — never `width`, `height`, `top`.
Exit faster than enter. Animate 1–2 elements per view. Every animation
interruptible. `prefers-reduced-motion` is already honoured globally in
`index.css` and must stay.

---

## 19. Responsive

Tailwind defaults: `sm` 640 · `md` 768 · `lg` 1024 · `xl` 1280.
Test at **375 / 768 / 1024 / 1440**.

**The rule: render a different component below `sm` — do not shrink the table.**

| Surface | Desktop | Mobile | Status |
|---|---|---|---|
| Patients | table | card list | exists |
| Appointments | Day/Week/Month grid | agenda list | exists |
| Call logs | master + detail | list → full-screen sheet | new |
| Admin tables (9) | table | card list | new |
| **Admin navigation** | sidebar | **drawer** | **new — P0** |
| Dialogs | centred | bottom sheet | new |

Plus: 44×44 minimum touch targets · `min-h-dvh` over `100vh` · no horizontal
page scroll at 375px · wide content scrolls inside its own container.

---

## 20. Accessibility baseline

Target **WCAG 2.1 AA**. Already in place and to be preserved: skip link, dialog
focus traps, `focus-visible` rings, `prefers-reduced-motion`, `aria-invalid` /
`aria-describedby` wiring, keyboard-operable table rows, `role="status"` on the
connection indicator.

Non-negotiable for anything new:

1. Keyboard reachable and operable, in visual order.
2. Visible focus, ≥3:1 against its ground.
3. Text ≥4.5:1; UI boundaries and data marks ≥3:1.
4. Never colour alone.
5. Icon-only controls have an accessible name; decorative icons are
   `aria-hidden`.
6. Live regions are polite and never steal focus.
7. Focus moves to `#main-content` on route change.

---

## 21. Conventions for contributors

1. **No hex literals in components.** Two remain in `src/`; add no more.
2. **No `dark:` variants.** Add a token and let both themes resolve it.
3. **Use the primitive.** If it needs a variant, add the variant to the primitive.
4. **Every new token is declared in both themes**, and its contrast is measured
   before it ships.
5. **Presentation only.** Do not change a handler, an endpoint, a validation rule
   or an event name while restyling.
6. **No new dependencies.**

---

## 21a. Native controls

Checkboxes, radios and file inputs stay **native**. A `border-*` colour class
on a native checkbox sets nothing — it has no width utility, and the browser
draws its own widget regardless; four such classes in this codebase had never
rendered anything. `accent-color` is the one property a native checkbox
honours.

The consequence that matters: the 16px box is never the touch target. **Wrap
it in a `min-h-11` label**, which supplies both the 44px hit area and the
accessible name.

---

## 22. Reference: complete primitive inventory

**Existing (20):** `Card` · `SectionHeader` · `Chip` · `LiveDot` · `Button` ·
`LinkButton` · `Field` · `Input` · `Textarea` · `PasswordInput` · `Select` ·
`InlineAlert` · `Table` · `THead` · `TH` · `TBody` · `TR` · `TD` · `EmptyState` ·
`ErrorState` · `Skeleton` · `Modal` · `ConfirmDialog`

**New (Phase 1):** `ToastProvider` + `useToast` · `Tabs` · `Menu` · `Tooltip` ·
`SortableTH` · `SkeletonTable` / `SkeletonCard` / `SkeletonList` ·
`BarChart` · `Sparkline` · `AxisY` · `ChartTooltip` · `ChartEmpty` · `ChartSkeleton` ·
`Button size="icon"`

All exported from `src/components/ui.jsx` or a sibling module imported through it,
so a page's import list stays one line.
