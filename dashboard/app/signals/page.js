"use client";

import { fmtTime, useLiveData } from "../../lib/api";

function regimeLabel(v) {
  if (v == null) return { text: "NO SIGNAL", cls: "" };
  if (v >= 0.85) return { text: "RISK ON", cls: "filled" };
  if (v >= 0.6) return { text: "CAUTIOUS", cls: "" };
  return { text: "RISK OFF", cls: "vetoed" };
}

export default function Signals() {
  const { data: signals } = useLiveData("/signals", {
    intervalMs: 15000,
    eventTypes: ["ResearchSignal"],
  });

  const regime = signals?.find((s) => s.kind === "regime" && s.key === "market");
  const tilts = (signals || []).filter((s) => s.kind === "symbol_tilt");
  const earnings = (signals || []).filter((s) => s.kind === "earnings");
  const label = regimeLabel(regime?.value);

  return (
    <>
      <div className="panel">
        <h2>Market regime — Claude researcher</h2>
        {regime ? (
          <>
            <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
              <span className={`tag ${label.cls}`} style={{ fontSize: 13, padding: "6px 14px" }}>{label.text}</span>
              <span className="mono">risk appetite {regime.value.toFixed(2)}</span>
              <span className="mono" style={{ color: "var(--muted)" }}>
                confidence {(regime.confidence * 100).toFixed(0)}% · {fmtTime(regime.ts)} · {regime.source_model}
              </span>
            </div>
            <div className="journal-text">{regime.reasoning}</div>
            <p style={{ color: "var(--muted)", fontSize: 12.5, marginBottom: 0 }}>
              This value scales the strategy&apos;s gross exposure, clamped to [0.30, 1.00] by the engine —
              the researcher can de-risk but never lever up, and a signal older than 36h decays to neutral.
            </p>
          </>
        ) : (
          <div className="empty">
            No research signal yet — <span className="mono">uv run python -m driftline.cognition.daemon --once research</span>
          </div>
        )}
      </div>

      <div className="panel">
        <h2>Earnings signals — Claude analyst (EDGAR 8-Ks)</h2>
        <div className="tablewrap">
          {earnings.length ? (
            <table>
              <thead>
                <tr>
                  <th>Symbol</th><th className="r">Score</th><th className="r">Confidence</th>
                  <th>Reasoning</th><th>As of</th>
                </tr>
              </thead>
              <tbody>
                {earnings.sort((a, b) => b.value - a.value).map((e) => (
                  <tr key={e.key}>
                    <td className="mono">{e.key}</td>
                    <td className={`mono r ${e.value > 0.1 ? "pos" : e.value < -0.1 ? "neg" : ""}`}>
                      {e.value > 0 ? "+" : ""}{e.value.toFixed(2)}
                    </td>
                    <td className="mono r">{(e.confidence * 100).toFixed(0)}%</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 480 }}>{e.reasoning}</td>
                    <td className="mono">{fmtTime(e.ts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">
              No earnings signals — none of the watchlist filed an earnings 8-K in the last few days,
              or the analyst hasn&apos;t run yet.
            </div>
          )}
        </div>
        <p style={{ color: "var(--muted)", fontSize: 12.5, marginBottom: 0 }}>
          Scores ≥ +0.50 with confidence ≥ 50% trigger a 5% post-earnings-drift entry
          (max 4 concurrent, ~12 session hold). Negative scores are only ever avoided — never shorted.
        </p>
      </div>

      <div className="panel">
        <h2>Symbol tilts</h2>
        <div className="tablewrap">
          {tilts.length ? (
            <table>
              <thead>
                <tr>
                  <th>Symbol</th><th className="r">Tilt</th><th className="r">Confidence</th>
                  <th>Reasoning</th><th>As of</th>
                </tr>
              </thead>
              <tbody>
                {tilts.sort((a, b) => b.value - a.value).map((t) => (
                  <tr key={t.key}>
                    <td className="mono">{t.key}</td>
                    <td className={`mono r ${t.value > 0.1 ? "pos" : t.value < -0.1 ? "neg" : ""}`}>
                      {t.value > 0 ? "+" : ""}{t.value.toFixed(2)}
                    </td>
                    <td className="mono r">{(t.confidence * 100).toFixed(0)}%</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 480 }}>{t.reasoning}</td>
                    <td className="mono">{fmtTime(t.ts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">No symbol tilts yet.</div>
          )}
        </div>
      </div>
    </>
  );
}
