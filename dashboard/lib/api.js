"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const API = "http://127.0.0.1:8484";

export async function fetchJSON(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

/** Poll an endpoint on an interval, and refetch instantly on live bus events. */
export function useLiveData(path, { intervalMs = 5000, eventTypes = null } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(() => {
    fetchJSON(path)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message));
  }, [path]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, intervalMs);

    let ws;
    let alive = true;
    const connect = () => {
      ws = new WebSocket(`${API.replace("http", "ws")}/ws`);
      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data);
          if (!eventTypes || eventTypes.includes(event.type)) refresh();
        } catch { /* ignore malformed frames */ }
      };
      ws.onclose = () => { if (alive) setTimeout(connect, 3000); };
    };
    try { connect(); } catch { /* engine offline; polling covers it */ }

    return () => { alive = false; clearInterval(timer); ws?.close(); };
  }, [path, intervalMs, refresh, eventTypes]);

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
