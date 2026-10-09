import { createContext, useCallback, useContext, useEffect, useState } from "react";
import * as adminApi from "./adminApi";

const AdminAuthContext = createContext(null);

export function AdminAuthProvider({ children }) {
  const [admin, setAdmin] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadAdmin = useCallback(async () => {
    if (!adminApi.getAdminToken()) {
      setAdmin(null);
      setLoading(false);
      return;
    }
    // Same rule as lib/auth.jsx: only a 401 ends the session (adminApi
    // clears the token on one); a 500 or network blip is retried once and
    // never signs the admin out.
    let me = null;
    for (let attempt = 0; attempt < 2 && !me; attempt++) {
      try {
        me = await adminApi.adminMe();
      } catch (err) {
        if (err?.status === 401) break;
        if (attempt === 0) await new Promise((r) => setTimeout(r, 1200));
      }
    }
    setAdmin(me);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadAdmin();
  }, [loadAdmin]);

  async function signIn(email, password) {
    await adminApi.adminLogin(email, password);
    await loadAdmin();
  }

  function signOut() {
    adminApi.adminLogout();
    setAdmin(null);
  }

  return (
    <AdminAuthContext.Provider value={{ admin, loading, signIn, signOut }}>
      {children}
    </AdminAuthContext.Provider>
  );
}

export function useAdminAuth() {
  const ctx = useContext(AdminAuthContext);
  if (!ctx) throw new Error("useAdminAuth must be used within AdminAuthProvider");
  return ctx;
}
