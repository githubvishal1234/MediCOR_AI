import { Navigate, useLocation } from "react-router-dom";
import { useAdminAuth } from "./adminAuth";

export default function AdminProtectedRoute({ children }) {
  const { admin, loading } = useAdminAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-low">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-navy" />
      </div>
    );
  }

  if (!admin) {
    return <Navigate to="/admin/login" state={{ from: location }} replace />;
  }

  return children;
}
