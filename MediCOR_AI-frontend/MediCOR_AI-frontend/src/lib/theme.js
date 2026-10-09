import { useEffect, useState } from "react";

/**
 * Light/dark theme, shared by the dashboard shell and the marketing navbar.
 *
 * The class goes on <html>, so whichever surface toggles it themes the whole
 * document — that is deliberate now, but it used to be an accident: only
 * AppShell had a toggle, so a visitor to the landing page inherited whatever
 * a logged-in user had last chosen and had no control of their own.
 *
 * Lives here rather than in either component because two copies of this would
 * drift, and the localStorage key has to match for the two toggles to agree.
 */
const STORAGE_KEY = "medvoice-theme";

export function useDarkMode() {
  const [dark, setDark] = useState(() => {
    if (typeof window === "undefined") return false;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) return stored === "dark";
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    window.localStorage.setItem(STORAGE_KEY, dark ? "dark" : "light");
  }, [dark]);

  return [dark, setDark];
}
