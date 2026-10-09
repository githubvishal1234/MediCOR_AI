/**
 * Low-level dashboard WebSocket client (Phase 6).
 *
 * Thin wrapper around the browser WebSocket API that talks to the
 * backend's /ws/dashboard endpoint (see app/realtime.py + app/routers/ws.py).
 * Handles auth (token as a query param — browsers can't set headers on
 * the WS handshake), auto-reconnect with backoff, and a simple
 * event-type pub/sub so callers don't need to know about the raw
 * `{ type, data, ts }` envelope.
 *
 * This module has no React dependency — src/lib/realtime.jsx wraps it
 * in a provider/hook for components to consume.
 */

import { getToken } from "./api";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export const STATUS = {
  IDLE: "idle", // no token yet / not connected on purpose
  CONNECTING: "connecting",
  OPEN: "open",
  CLOSED: "closed", // disconnected, will retry unless manually closed
};

class DashboardSocket {
  constructor() {
    this._ws = null;
    this._listeners = new Map(); // event type -> Set<handler>
    this._statusListeners = new Set();
    this._reconnectAttempt = 0;
    this._reconnectTimer = null;
    this._manuallyClosed = true;
    this._status = STATUS.IDLE;
  }

  get status() {
    return this._status;
  }

  _setStatus(status) {
    if (this._status === status) return;
    this._status = status;
    this._statusListeners.forEach((fn) => {
      try {
        fn(status);
      } catch {
        // a bad listener shouldn't take down the socket
      }
    });
  }

  /** Opens the connection (no-op if already open/connecting or no auth token yet). */
  connect() {
    if (this._ws && (this._status === STATUS.OPEN || this._status === STATUS.CONNECTING)) return;
    const token = getToken();
    if (!token) {
      this._setStatus(STATUS.IDLE);
      return;
    }
    this._manuallyClosed = false;
    this._open(token);
  }

  _open(token) {
    this._setStatus(STATUS.CONNECTING);
    let ws;
    try {
      ws = new WebSocket(`${WS_BASE}/ws/dashboard?token=${encodeURIComponent(token)}`);
    } catch {
      this._setStatus(STATUS.CLOSED);
      this._scheduleReconnect();
      return;
    }
    this._ws = ws;

    ws.onopen = () => {
      this._reconnectAttempt = 0;
      this._setStatus(STATUS.OPEN);
    };

    ws.onmessage = (evt) => {
      let message;
      try {
        message = JSON.parse(evt.data);
      } catch {
        return;
      }
      const handlers = this._listeners.get(message.type);
      if (handlers) handlers.forEach((fn) => fn(message.data, message));
      const wildcard = this._listeners.get("*");
      if (wildcard) wildcard.forEach((fn) => fn(message.data, message));
    };

    ws.onclose = () => {
      if (this._ws !== ws) return; // stale socket from a previous attempt
      this._ws = null;
      this._setStatus(STATUS.CLOSED);
      if (!this._manuallyClosed) this._scheduleReconnect();
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  _scheduleReconnect() {
    clearTimeout(this._reconnectTimer);
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this._reconnectAttempt, RECONNECT_MAX_MS);
    this._reconnectAttempt += 1;
    this._reconnectTimer = setTimeout(() => {
      if (this._manuallyClosed) return;
      const token = getToken();
      if (!token) {
        this._setStatus(STATUS.IDLE);
        return;
      }
      this._open(token);
    }, delay);
  }

  /** Closes the connection and stops auto-reconnecting until connect() is called again. */
  disconnect() {
    this._manuallyClosed = true;
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = null;
    if (this._ws) {
      const ws = this._ws;
      this._ws = null;
      ws.onclose = null;
      ws.close();
    }
    this._setStatus(STATUS.IDLE);
  }

  /** Subscribe to a message type (e.g. "call.started", "notification", or "*" for all). Returns an unsubscribe fn. */
  on(eventType, handler) {
    if (!this._listeners.has(eventType)) this._listeners.set(eventType, new Set());
    this._listeners.get(eventType).add(handler);
    return () => this._listeners.get(eventType)?.delete(handler);
  }

  onStatusChange(handler) {
    this._statusListeners.add(handler);
    return () => this._statusListeners.delete(handler);
  }
}

// Single shared socket for the whole app — every dashboard tab/component
// subscribes to the same connection rather than opening its own.
export const dashboardSocket = new DashboardSocket();