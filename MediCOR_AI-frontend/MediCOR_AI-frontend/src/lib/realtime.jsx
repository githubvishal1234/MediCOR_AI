/**
 * Dashboard real-time context (Phase 6).
 *
 * Wraps the shared dashboardSocket (src/lib/ws.js) in a React provider so
 * any page under AppShell can read live connection status, staff
 * notifications, and in-progress calls without managing the WebSocket
 * itself. Mounted once in AppShell — see components/AppShell.jsx.
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { dashboardSocket, STATUS } from "./ws";
import { getToken } from "./api";
import * as api from "./api";

const RealtimeContext = createContext(null);

const MAX_NOTIFICATIONS = 30;
const TERMINAL_CALL_STATUSES = new Set(["completed", "failed", "no_answer"]);

/**
 * A call only belongs in "Live Active Calls" if it is genuinely in progress
 * *right now*. In addition to status/ended_at (already checked elsewhere),
 * guard against stale/demo rows whose start timestamp isn't today — a real
 * in-progress call is always started the same calendar day it's viewed.
 */
function isStartedToday(call) {
  const startedRaw = call.started_at || call.occurred_at;
  if (!startedRaw) return true; // no timestamp to judge by — don't hide it
  const started = new Date(startedRaw);
  if (Number.isNaN(started.getTime())) return true;
  const now = new Date();
  return (
    started.getFullYear() === now.getFullYear() &&
    started.getMonth() === now.getMonth() &&
    started.getDate() === now.getDate()
  );
}

function isGenuinelyActive(call) {
  return !TERMINAL_CALL_STATUSES.has(call.status) && !call.ended_at && isStartedToday(call);
}

export function RealtimeProvider({ children }) {
  const [status, setStatus] = useState(dashboardSocket.status);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [liveCalls, setLiveCalls] = useState({}); // id -> call summary

  useEffect(() => {
    if (!getToken()) return undefined;

    dashboardSocket.connect();
    const offStatus = dashboardSocket.onStatusChange(setStatus);

    const offNotification = dashboardSocket.on("notification", (data) => {
      const entry = {
        // Since HV-6 the backend persists first and sends the stored row's
        // id, so a live notification and the same one fetched from history
        // are the same object — that id is what lets us mark it read and
        // what stops it appearing twice after a reload. Falling back to a
        // synthetic id keeps this working if persistence failed (notify()
        // is deliberately non-fatal, so `id` can be null).
        id: data.id || `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        persisted: Boolean(data.id),
        receivedAt: new Date().toISOString(),
        level: data.level || "info",
        title: data.title,
        message: data.message,
        data: data.data || {},
        read_at: null,
      };
      setNotifications((prev) => {
        if (prev.some((n) => n.id === entry.id)) return prev;
        return [entry, ...prev].slice(0, MAX_NOTIFICATIONS);
      });
      setUnreadCount((c) => c + 1);
    });

    const upsertOrRemoveCall = (call) => {
      setLiveCalls((prev) => {
        // A call with a real ended_at timestamp, a terminal status, or a
        // start date that isn't today is never "live" — ended_at/status are
        // set once, by the backend, only when the call actually finished
        // (see app/routers/calls.py); the today-check guards against any
        // stale/demo record whose status was never correctly finalized.
        if (!isGenuinelyActive(call)) {
          if (!(call.id in prev)) return prev;
          const next = { ...prev };
          delete next[call.id];
          return next;
        }
        return { ...prev, [call.id]: call };
      });
    };

    const offStarted = dashboardSocket.on("call.started", upsertOrRemoveCall);
    const offUpdated = dashboardSocket.on("call.updated", upsertOrRemoveCall);
    const offEnded = dashboardSocket.on("call.ended", upsertOrRemoveCall);

    return () => {
      offStatus();
      offNotification();
      offStarted();
      offUpdated();
      offEnded();
      dashboardSocket.disconnect();
    };
  }, []);

  // HV-6: load stored history once on mount, so a refresh no longer wipes
  // the list and anything raised while nobody had the dashboard open is
  // still here. Live events arriving before this resolves are preserved —
  // the merge below keeps them and de-duplicates by id.
  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    api
      .listNotifications({ limit: MAX_NOTIFICATIONS })
      .then((page) => {
        if (cancelled) return;
        const stored = page.items.map((n) => ({
          ...n,
          persisted: true,
          receivedAt: n.created_at,
        }));
        setNotifications((live) => {
          const seen = new Set(stored.map((n) => n.id));
          const liveOnly = live.filter((n) => !seen.has(n.id));
          return [...liveOnly, ...stored].slice(0, MAX_NOTIFICATIONS);
        });
        setUnreadCount(page.unread_count);
      })
      .catch(() => {
        // History is an enhancement — a failure here leaves the live-only
        // behaviour that existed before HV-6, which is still usable.
      });
    return () => { cancelled = true; };
  }, []);

  const markAllRead = useCallback(() => {
    setUnreadCount(0);
    setNotifications((prev) =>
      prev.map((n) => (n.read_at ? n : { ...n, read_at: new Date().toISOString() }))
    );
    // Fire-and-forget: the badge is already cleared locally, and a failed
    // write just means it reappears on next load rather than blocking the UI.
    api.markAllNotificationsRead().catch(() => {});
  }, []);

  const dismissNotification = useCallback((id) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
    // Only server-known notifications can be acknowledged server-side.
    if (!String(id).startsWith("local-")) {
      api.markNotificationRead(id).catch(() => {});
    }
  }, []);

  /** Subscribe to any raw event ("appointment.booked", "call.transcript_message", "*", ...). */
  const subscribe = useCallback((eventType, handler) => dashboardSocket.on(eventType, handler), []);

  /** Manually re-sync liveCalls from a fresh REST fetch (e.g. list_calls filtered to in_progress). */
  const seedLiveCalls = useCallback((calls) => {
    setLiveCalls(() => {
      const next = {};
      for (const c of calls) {
        if (isGenuinelyActive(c)) next[c.id] = c;
      }
      return next;
    });
  }, []);

  const value = {
    connected: status === STATUS.OPEN,
    status,
    notifications,
    unreadCount,
    markAllRead,
    dismissNotification,
    liveCalls: Object.values(liveCalls).sort(
      (a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0)
    ),
    subscribe,
    seedLiveCalls,
  };

  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtime() {
  const ctx = useContext(RealtimeContext);
  if (!ctx) throw new Error("useRealtime must be used within RealtimeProvider");
  return ctx;
}