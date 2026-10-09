# MediCOR AI

Marketing site + product dashboard for "MediCOR AI" — an autonomous, HIPAA-compliant
AI voice receptionist for clinics. Built with React, React Router, and Tailwind CSS v4, based
on the provided Stitch design mockups.

## Getting started

```bash
npm install
cp .env.example .env   # optional — only if the API isn't on localhost:8000
npm run dev
```

Then open the printed local URL (typically http://localhost:5173).

This app is **not** standalone — it needs the FastAPI backend running. Start
that first (`uvicorn app.main:app --reload --port 8000` from the backend
directory), or every page loads into an error state.

`VITE_API_URL` points at the backend and is the only configuration this app
has. It also determines the dashboard WebSocket URL (`src/lib/ws.js` swaps
`http`→`ws`). See `.env.example`.

## Build for production

```bash
npm run build
npm run preview
```

## Structure

- `src/pages/marketing/Landing.jsx` – the public marketing page (`/`)
- `src/pages/app/*` – the product dashboard, mounted under `/app/*`:
  - `/app` – Overview
  - `/app/calls` – Call Logs & Transcripts
  - `/app/appointments` – Appointment Manager
  - `/app/patients` and `/app/patients/:id` – Patients & Patient Profile
  - `/app/ehr` – EHR Integration Hub
  - `/app/agent` – AI Agent Settings
  - `/app/security` – Security & Compliance
  - `/app/billing` – Billing & Usage
- `src/pages/admin/*` – the Super Admin console, mounted under `/admin/*` (organizations,
  plans, subscriptions, usage, phone numbers, audit log) with its own separate login
- `src/components/AppShell.jsx` – shared dashboard sidebar/topbar layout
- `src/components/AdminShell.jsx` – the equivalent shell for the Super Admin console
- `src/components/marketing/*` – marketing page sections
- `src/components/ui.jsx` – shared Card / Chip / Button primitives
- `src/lib/api.js` – clinic API client (all dashboard calls go through here)
- `src/lib/adminApi.js` – Super Admin API client, with its own token storage key
- `src/lib/ws.js` + `src/lib/realtime.jsx` – dashboard WebSocket and its React provider
- `src/index.css` – design tokens (colors, type, radii) as Tailwind v4 `@theme` variables

## Notes

- All dashboard pages share one consistent sidebar/nav — the original mockups had two
  slightly different sidebar variants across screens, so these were unified into a single
  navigation structure for a coherent, working app.
- **The dashboard runs on live API data.** An earlier version of this README described mock
  data in `src/lib/data.js`; that file was removed once the pages were wired to the backend.
  Add new calls as wrappers in `src/lib/api.js` rather than calling `fetch` from a component.
- Two independent auth sessions coexist by design: the clinic dashboard stores its JWT under
  `medvoice_token`, the Super Admin console under `medvoice_admin_token`, so one browser can
  hold both at once.
- Fonts (Plus Jakarta Sans, Inter) load from Google Fonts via `src/index.css`.
- Lint with `npm run lint` — this project uses **oxlint**, not ESLint.
