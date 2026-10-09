/**
 * Render smoke check — the frontend's only automated test.
 *
 * There is no test runner in this project and this deliberately does not
 * add one: it uses `react-dom/server`, which is already a dependency, and
 * Vite's SSR build, which is already how the app is compiled. `npm run
 * smoke` is the whole contract.
 *
 * WHAT IT IS FOR
 *
 * Every page is rendered to a string. That is a narrow check — effects do
 * not run, so nothing here talks to the API — but it catches the class of
 * defect that a build does NOT: a component that throws on its first
 * frame, a hook used against its contract, a required provider missing,
 * and stray text that was never meant to be rendered.
 *
 * It has already earned its place twice. It found eleven instances of an
 * attribute stranded outside its tag (`<Icon size={16} /> className="…"`),
 * which React renders as literal visible text — one of them inside the
 * landing page's main headline — and it found a component calling a
 * render-prop child as though it took ordinary children.
 *
 * WHAT IT IS NOT
 *
 * Not a substitute for a real test suite. It asserts that pages render,
 * not that they render correctly. Interaction, state and API behaviour
 * are all outside it.
 */

import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../src/lib/auth";
import { AdminAuthProvider } from "../src/lib/adminAuth";
import { RealtimeProvider } from "../src/lib/realtime";

import Login from "../src/pages/Login";
import AcceptInvite from "../src/pages/AcceptInvite";
import ResetPassword from "../src/pages/ResetPassword";
import PatientAppointment from "../src/pages/PatientAppointment";
import NotFound from "../src/pages/NotFound";
import Landing from "../src/pages/marketing/Landing";
import Privacy from "../src/pages/marketing/Privacy";
import Terms from "../src/pages/marketing/Terms";
import Contact from "../src/pages/marketing/Contact";
import AdminLogin from "../src/pages/admin/AdminLogin";

import Dashboard from "../src/pages/app/Dashboard";
import CallLogs from "../src/pages/app/CallLogs";
import CallQuality from "../src/pages/app/CallQuality";
import Patients from "../src/pages/app/Patients";
import PatientProfile from "../src/pages/app/PatientProfile";
import Appointments from "../src/pages/app/Appointments";
import EHRIntegration from "../src/pages/app/EHRIntegration";
import AgentSettings from "../src/pages/app/AgentSettings";
import KnowledgeBase from "../src/pages/app/KnowledgeBase";
import ClinicProfile from "../src/pages/app/ClinicProfile";
import Team from "../src/pages/app/Team";
import Analytics from "../src/pages/app/Analytics";
import Billing from "../src/pages/app/Billing";
import Security from "../src/pages/app/Security";
import Support from "../src/pages/app/Support";

import AdminOverview from "../src/pages/admin/AdminOverview";
import AdminOrganizations from "../src/pages/admin/AdminOrganizations";
import AdminOrgDetail from "../src/pages/admin/AdminOrgDetail";
import AdminUsers from "../src/pages/admin/AdminUsers";
import AdminPlans from "../src/pages/admin/AdminPlans";
import AdminSubscriptions from "../src/pages/admin/AdminSubscriptions";
import AdminUsage from "../src/pages/admin/AdminUsage";
import AdminPhoneNumbers from "../src/pages/admin/AdminPhoneNumbers";
import AdminSupport from "../src/pages/admin/AdminSupport";
import AdminAuditLog from "../src/pages/admin/AdminAuditLog";

import {
  Field,
  Input,
  Select,
  Textarea,
  PasswordInput,
  Modal,
  ConfirmDialog,
  Table,
  THead,
  TH,
  TBody,
  TD,
  InlineAlert,
  EmptyState,
  ErrorState,
  SortableTH,
  SkeletonTable,
  SkeletonList,
  SkeletonCard,
  ToastProvider,
  Tabs,
  TabPanel,
  Menu,
  MenuItem,
  Tooltip,
  BarChart,
  Sparkline,
  ChartEmpty,
  ChartSkeleton,
  Button,
} from "../src/components/ui";

/** A clinic page: router + both contexts the AppShell reads. */
const clinic = (El) => (
  <MemoryRouter>
    <AuthProvider>
      <RealtimeProvider>
        <El />
      </RealtimeProvider>
    </AuthProvider>
  </MemoryRouter>
);

/** A platform-console page: its own, separate auth context. */
const admin = (El) => (
  <MemoryRouter>
    <AdminAuthProvider>
      <El />
    </AdminAuthProvider>
  </MemoryRouter>
);

const publicPage = (El) => (
  <MemoryRouter>
    <El />
  </MemoryRouter>
);

/**
 * Identity, for the primitive cases below.
 *
 * They are rendered one at a time, never as a React list, but a bare JSX
 * literal sitting in an array reads to the linter as a list that has
 * forgotten its keys. Passing each through a call sidesteps that without
 * inventing keys nothing uses.
 */
const ui = (element) => element;

const CASES = [
  // --- public / auth
  ["Landing", publicPage(Landing)],
  ["Privacy", publicPage(Privacy)],
  ["Terms", publicPage(Terms)],
  ["Contact", publicPage(Contact)],
  ["Login", publicPage(() => <AuthProvider><Login /></AuthProvider>)],
  ["AcceptInvite", publicPage(AcceptInvite)],
  ["ResetPassword", publicPage(ResetPassword)],
  ["PatientAppointment", publicPage(PatientAppointment)],
  ["NotFound", publicPage(NotFound)],
  ["AdminLogin", admin(AdminLogin)],

  // --- clinic dashboard
  ["Dashboard", clinic(Dashboard)],
  ["CallLogs", clinic(CallLogs)],
  ["CallQuality", clinic(CallQuality)],
  ["Patients", clinic(Patients)],
  ["PatientProfile", clinic(PatientProfile)],
  ["Appointments", clinic(Appointments)],
  ["EHRIntegration", clinic(EHRIntegration)],
  ["AgentSettings", clinic(AgentSettings)],
  ["KnowledgeBase", clinic(KnowledgeBase)],
  ["ClinicProfile", clinic(ClinicProfile)],
  ["Team", clinic(Team)],
  ["Analytics", clinic(Analytics)],
  ["Billing", clinic(Billing)],
  ["Security", clinic(Security)],
  ["Support", clinic(Support)],

  // --- platform console
  ["AdminOverview", admin(AdminOverview)],
  ["AdminOrganizations", admin(AdminOrganizations)],
  ["AdminOrgDetail", admin(AdminOrgDetail)],
  ["AdminUsers", admin(AdminUsers)],
  ["AdminPlans", admin(AdminPlans)],
  ["AdminSubscriptions", admin(AdminSubscriptions)],
  ["AdminUsage", admin(AdminUsage)],
  ["AdminPhoneNumbers", admin(AdminPhoneNumbers)],
  ["AdminSupport", admin(AdminSupport)],
  ["AdminAuditLog", admin(AdminAuditLog)],

  // --- shared primitives, in both of Field's call shapes
  ["ui: Field + element child", ui(<Field label="Email" hint="h"><Input /></Field>)],
  ["ui: Field + error", ui(<Field label="Email" error="bad"><Input /></Field>)],
  ["ui: Field + render prop", ui(<Field label="X">{(p) => <Input {...p} />}</Field>)],
  ["ui: Field + Select", ui(<Field label="TZ"><Select><option>UTC</option></Select></Field>)],
  ["ui: Field + Textarea", ui(<Field label="T"><Textarea /></Field>)],
  ["ui: Field + PasswordInput", ui(<Field label="P"><PasswordInput /></Field>)],
  ["ui: Modal", ui(<Modal open title="T" onClose={() => {}}>body</Modal>)],
  ["ui: ConfirmDialog", ui(<ConfirmDialog open title="T" onConfirm={() => {}} onCancel={() => {}} />)],
  [
    "ui: Table",
    ui(
      <Table>
        <THead><tr><TH>A</TH></tr></THead>
        <TBody><tr><TD>1</TD></tr></TBody>
      </Table>
    ),
  ],
  ["ui: InlineAlert", ui(<InlineAlert>problem</InlineAlert>)],
  ["ui: EmptyState", ui(<EmptyState title="Nothing" detail="here" />)],
  ["ui: ErrorState", ui(<ErrorState detail="broke" onRetry={() => {}} />)],

  // --- primitives added in the Phase 1 design-system pass
  [
    "ui: SortableTH",
    ui(
      <Table>
        <THead>
          <tr><SortableTH sortKey="name" active="name" direction="asc" onSort={() => {}}>Name</SortableTH></tr>
        </THead>
        <TBody><tr><TD>1</TD></tr></TBody>
      </Table>
    ),
  ],
  ["ui: Button size=icon", ui(<Button size="icon" aria-label="Close">x</Button>)],
  ["ui: SkeletonTable", ui(<SkeletonTable rows={2} cols={3} />)],
  ["ui: SkeletonList", ui(<SkeletonList rows={2} />)],
  ["ui: SkeletonCard", ui(<SkeletonCard />)],
  // The provider must render its children even with nothing queued, and
  // useToast() must not throw when no provider is mounted -- both are
  // load-bearing, since a missing provider must never break a page.
  ["ui: ToastProvider", ui(<ToastProvider><p>body</p></ToastProvider>)],
  [
    "ui: Tabs + TabPanel",
    ui(
      <div>
        <Tabs items={[{ id: "a", label: "A" }, { id: "b", label: "B" }]} value="a" onChange={() => {}} />
        <TabPanel id="a" value="a">panel</TabPanel>
      </div>
    ),
  ],
  [
    "ui: Tabs underline",
    ui(<Tabs variant="underline" items={[{ id: "a", label: "A" }]} value="a" onChange={() => {}} />),
  ],
  [
    "ui: Menu",
    ui(
      <Menu trigger={<button type="button">Open</button>}>
        <MenuItem>One</MenuItem>
      </Menu>
    ),
  ],
  ["ui: Tooltip", ui(<Tooltip content="hi"><button type="button">t</button></Tooltip>)],
  [
    "ui: BarChart",
    ui(
      <BarChart
        caption="Calls per hour"
        unit="calls"
        rows={[{ label: "9AM", value: 3 }, { label: "10AM", value: 0 }, { label: "11AM", value: null }]}
      />
    ),
  ],
  [
    "ui: Sparkline",
    ui(<Sparkline caption="Trend" rows={[{ label: "Mon", value: 1 }, { label: "Tue", value: 4 }]} />),
  ],
  ["ui: ChartEmpty", ui(<ChartEmpty />)],
  ["ui: ChartSkeleton", ui(<ChartSkeleton bars={4} />)],
];

/**
 * Text that should never reach the page.
 *
 * `className=` is the one that has actually bitten: an attribute written
 * outside its tag is not a syntax error, it is a text node, and it ships
 * looking like a typo nobody notices. The rest are the usual render slips
 * — a missing field, a bad number, an object where a string belongs.
 */
const FORBIDDEN = [
  [/className=&quot;|className="/, "a stray className attribute rendered as text"],
  [/\[object Object\]/, "an object rendered as text"],
  [/\bundefined\b/, "the literal string 'undefined'"],
  [/\bNaN\b/, "the literal string 'NaN'"],
];

let failures = 0;
let checked = 0;

for (const [name, element] of CASES) {
  checked += 1;
  let html;
  try {
    html = renderToString(element);
  } catch (err) {
    failures += 1;
    console.log(`FAIL   ${name.padEnd(22)} ${err.message}`);
    continue;
  }

  const problems = FORBIDDEN.filter(([re]) => re.test(html)).map(([, why]) => why);
  if (problems.length) {
    failures += 1;
    console.log(`BAD    ${name.padEnd(22)} ${problems.join("; ")}`);
  } else {
    console.log(`ok     ${name.padEnd(22)} ${html.length}b`);
  }
}

console.log(
  failures === 0
    ? `\nAll ${checked} rendered cleanly.`
    : `\n${failures} of ${checked} FAILED.`
);
process.exit(failures === 0 ? 0 : 1);
