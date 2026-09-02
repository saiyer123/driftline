"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const API = "http://127.0.0.1:8484";

export async function fetchJSON(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// ---- one shared WebSocket per tab; hooks subscribe to it ----
const listeners = new Set();
let socketStarted = false;

function ensureSocket() {
  if (socketStarted || typeof window === "undefined") return;
  socketStarted = true;
  const connect = () => {
    let ws;
    try {
      ws = new WebSocket(`${API.replace("http", "ws")}/ws`);
    } catch {
      setTimeout(connect, 5000);
      return;
    }
    ws.onmessage = (msg) => {
      let event;
      try { event = JSON.parse(msg.data); } catch { return; }
      listeners.forEach((fn) => fn(event));
    };
    ws.onclose = () => setTimeout(connect, 3000);
  };
  connect();
}

/** Poll an endpoint on an interval, and refetch instantly on live bus events.
 *  Effect dependencies are stable strings — a fresh array literal for
 *  eventTypes must never tear the effect down (that caused a refetch storm). */
export function useLiveData(path, { intervalMs = 5000, eventTypes = null } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const typesKey = eventTypes ? eventTypes.join(",") : "";
  const typesRef = useRef(eventTypes);
  typesRef.current = eventTypes;

  const refresh = useCallback(() => {
    fetchJSON(path)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message));
  }, [path]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, intervalMs);
    ensureSocket();
    const onEvent = (event) => {
      const types = typesRef.current;
      if (!types || types.includes(event.type)) refresh();
    };
    listeners.add(onEvent);
    return () => { clearInterval(timer); listeners.delete(onEvent); };
  }, [path, intervalMs, typesKey, refresh]);

  return { data, error, refresh };
}

export const fmtUsd = (v, { sign = false } = {}) => {
  if (v == null || Number.isNaN(v)) return "—";
  const s = sign && v > 0 ? "+" : v < 0 ? "−" : sign ? "±" : "";
  return `${s}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export const fmtQty = (v) => (v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 4 }));

export const fmtTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
};

export const pnlClass = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
