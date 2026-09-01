"use client";

import { API, useLiveData } from "../lib/api";

export default function StatusBar() {
  const { data: status, refresh } = useLiveData("/status", {
    intervalMs: 4000,
    eventTypes: ["HaltEvent", "ResumeEvent", "EquitySnapshot"],
  });

  const halted = status?.halted;

  const onKill = async () => {
    if (!window.confirm("Set the kill switch? The engine will reject all new orders until resumed.")) return;
    await fetch(`${API}/kill`, { method: "POST" });
    refresh();
  };
  const onResume = async () => {
    if (!window.confirm("Resume trading? Make sure you understand why it halted first.")) return;
    await fetch(`${API}/resume`, { method: "POST" });
    refresh();
  };

  return (
    <div className="topbar">
      <h1>Driftline</h1>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        {status ? (
          <span className={`pill ${halted ? "halted" : "running"}`}>
            <span className="dot" aria-hidden />
            {halted ? `HALTED${status.halt_reason ? ` — ${status.halt_reason}` : ""}` : "RUNNING · PAPER"}
          </span>
        ) : (
          <span className="pill">
            <span className="dot" style={{ background: "var(--muted)" }} aria-hidden />
            ENGINE OFFLINE
          </span>
        )}
        {status && (halted ? (
          <button className="killbtn resume" onClick={onResume}>RESUME</button>
        ) : (
          <button className="killbtn" onClick={onKill}>KILL SWITCH</button>
        ))}
      </div>
    </div>
  );
}
