import { BrowserRouter, Routes, Route } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary";
import NotFound from "./pages/NotFound";
import { AuthProvider } from "./lib/auth";
import ProtectedRoute from "./lib/ProtectedRoute";
import { AdminAuthProvider } from "./lib/adminAuth";
import AdminProtectedRoute from "./lib/AdminProtectedRoute";
import Landing from "./pages/marketing/Landing";
import Privacy from "./pages/marketing/Privacy";
import Terms from "./pages/marketing/Terms";
import Contact from "./pages/marketing/Contact";
import Login from "./pages/Login";
import Dashboard from "./pages/app/Dashboard";
import CallLogs from "./pages/app/CallLogs";
import Appointments from "./pages/app/Appointments";
import Patients from "./pages/app/Patients";
import PatientProfile from "./pages/app/PatientProfile";
import EHRIntegration from "./pages/app/EHRIntegration";
import AgentSettings from "./pages/app/AgentSettings";
import Security from "./pages/app/Security";
import Billing from "./pages/app/Billing";
import KnowledgeBase from "./pages/app/KnowledgeBase";
import ClinicProfile from "./pages/app/ClinicProfile";
import Support from "./pages/app/Support";
import Team from "./pages/app/Team";
import Analytics from "./pages/app/Analytics";
import AcceptInvite from "./pages/AcceptInvite";
import ResetPassword from "./pages/ResetPassword";
import PatientAppointment from "./pages/PatientAppointment";
import AdminLogin from "./pages/admin/AdminLogin";
import AdminOverview from "./pages/admin/AdminOverview";
import AdminOrganizations from "./pages/admin/AdminOrganizations";
import AdminOrgDetail from "./pages/admin/AdminOrgDetail";
import AdminUsers from "./pages/admin/AdminUsers";
import AdminPlans from "./pages/admin/AdminPlans";
import AdminSubscriptions from "./pages/admin/AdminSubscriptions";
import AdminUsage from "./pages/admin/AdminUsage";
import AdminPhoneNumbers from "./pages/admin/AdminPhoneNumbers";
import AdminSupport from "./pages/admin/AdminSupport";
import AdminAuditLog from "./pages/admin/AdminAuditLog";

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
        <AdminAuthProvider>
          <Routes>
            <Route path="/" element={<Landing />} />
            {/* Public marketing pages. These exist because the footer
                linked to #privacy, #terms and #contact -- anchors to
                sections that were never on the page, so all three
                scrolled nowhere. */}
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/terms" element={<Terms />} />
            <Route path="/contact" element={<Contact />} />
            <Route path="/login" element={<Login />} />
            <Route path="/accept-invite" element={<AcceptInvite />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            {/* Public, no account: the token in the URL is the only
                credential and it is scoped to one appointment. */}
            <Route path="/appointment" element={<PatientAppointment />} />
            <Route path="/app" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
            <Route path="/app/calls" element={<ProtectedRoute><CallLogs /></ProtectedRoute>} />
            <Route path="/app/appointments" element={<ProtectedRoute><Appointments /></ProtectedRoute>} />
            <Route path="/app/patients" element={<ProtectedRoute><Patients /></ProtectedRoute>} />
            <Route path="/app/patients/:id" element={<ProtectedRoute><PatientProfile /></ProtectedRoute>} />
            <Route path="/app/knowledge-base" element={<ProtectedRoute><KnowledgeBase /></ProtectedRoute>} />
            <Route path="/app/clinic" element={<ProtectedRoute><ClinicProfile /></ProtectedRoute>} />
            <Route path="/app/support" element={<ProtectedRoute><Support /></ProtectedRoute>} />
            <Route path="/app/team" element={<ProtectedRoute><Team /></ProtectedRoute>} />
            <Route path="/app/analytics" element={<ProtectedRoute><Analytics /></ProtectedRoute>} />
            <Route path="/app/ehr" element={<ProtectedRoute><EHRIntegration /></ProtectedRoute>} />
            <Route path="/app/agent" element={<ProtectedRoute><AgentSettings /></ProtectedRoute>} />
            <Route path="/app/security" element={<ProtectedRoute><Security /></ProtectedRoute>} />
            <Route path="/app/billing" element={<ProtectedRoute><Billing /></ProtectedRoute>} />

            {/* Super Admin — fully separate auth/session from the routes above */}
            <Route path="/admin/login" element={<AdminLogin />} />
            <Route path="/admin" element={<AdminProtectedRoute><AdminOverview /></AdminProtectedRoute>} />
            <Route
              path="/admin/organizations"
              element={<AdminProtectedRoute><AdminOrganizations /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/organizations/:id"
              element={<AdminProtectedRoute><AdminOrgDetail /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/users"
              element={<AdminProtectedRoute><AdminUsers /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/plans"
              element={<AdminProtectedRoute><AdminPlans /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/subscriptions"
              element={<AdminProtectedRoute><AdminSubscriptions /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/usage"
              element={<AdminProtectedRoute><AdminUsage /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/phone-numbers"
              element={<AdminProtectedRoute><AdminPhoneNumbers /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/support"
              element={<AdminProtectedRoute><AdminSupport /></AdminProtectedRoute>}
            />
            <Route
              path="/admin/audit-log"
              element={<AdminProtectedRoute><AdminAuditLog /></AdminProtectedRoute>}
            />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </AdminAuthProvider>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}