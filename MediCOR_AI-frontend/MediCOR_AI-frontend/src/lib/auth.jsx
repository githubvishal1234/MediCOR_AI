import { createContext, useCallback, useContext, useEffect, useState } from "react";
import * as api from "./api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    if (!api.getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    // Only a 401 ends the session, and api.request already clears the token
    // on one. A 500 or a network error is the server having a bad moment --
    // the database is on another machine -- so retry once and keep the token
    // either way. This used to clear it on ANY failure, which signed out
    // every open tab whenever one page load hit a slow database call.
    let me = null;
    for (let attempt = 0; attempt < 2 && !me; attempt++) {
      try {
        me = await api.me();
      } catch (err) {
        if (err?.status === 401) break;
        if (attempt === 0) await new Promise((r) => setTimeout(r, 1200));
      }
    }
    setUser(me);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  async function signIn(email, password) {
    await api.login(email, password);
    await loadUser();
  }

  async function signUp(fields) {
    await api.register(fields);
    await loadUser();
  }

  function signOut() {
    api.logout();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signUp, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}