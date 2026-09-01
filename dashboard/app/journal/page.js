"use client";

import { fmtTime, useLiveData } from "../../lib/api";

export default function Journal() {
  const { data: entries } = useLiveData("/journal", {
    intervalMs: 10000,
    eventTypes: ["JournalEntry"],
  });
  const { data: halts } = useLiveData("/halts", {
    intervalMs: 10000,
    eventTypes: ["HaltEvent", "ResumeEvent"],
  });

  return (
    <>
      <div className="panel">
        <h2>Decision journal</h2>
        {entries?.length ? (
          entries.map((e, i) => (
            <div className="journal-entry" key={i}>
              <div className="journal-meta">
                <span>{fmtTime(e.ts)}</span>
                <span>{e.strategy}@{e.strategy_version}</span>
                <span className="tag">{e.kind}</span>
              </div>
              <div className="journal-text">{e.text}</div>
            </div>
          ))
        ) : (
          <div className="empty">No journal entries yet — strategies write one per decision.</div>
        )}
      </div>

      <div className="panel">
        <h2>Halts &amp; resumes</h2>
        {halts?.length ? (
          halts.map((h, i) => (
            <div className="journal-entry" key={i}>
              <div className="journal-meta">
                <span>{fmtTime(h.ts)}</span>
                <span className={`tag ${h.action === "halt" ? "vetoed" : "filled"}`}>{h.action.toUpperCase()}</span>
                <span>{h.source}</span>
              </div>
              <div className="journal-text">{h.reason}</div>
            </div>
          ))
        ) : (
          <div className="empty">No halts recorded. Good.</div>
        )}
      </div>
    </>
  );
}
