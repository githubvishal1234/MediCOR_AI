import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./auth";

export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-low">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-accent" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return children;
}