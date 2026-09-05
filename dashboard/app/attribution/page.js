"use client";

import { fmtUsd, pnlClass, useLiveData } from "../../lib/api";

export default function Attribution() {
  const { data: rows } = useLiveData("/attribution", {
    intervalMs: 15000,
    eventTypes: ["Fill", "EquitySnapshot"],
  });

  return (
    <div className="panel">
      <h2>P&amp;L attribution by strategy version</h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        Realized P&amp;L (average cost, net of fees) per strategy version, plus unrealized P&amp;L on what that
        version still holds at current marks. Cash flow is not profit; this is.
      </p>
      <div className="tablewrap">
        {rows?.length ? (
          <table>
            <thead>
              <tr>
                <th>Strategy</th><th>Version</th>
                <th className="r">Fills</th><th className="r">Realized</th>
                <th className="r">Unrealized</th><th className="r">Total</th>
                <th className="r">Fees</th><th>Still holding</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.strategy}-${r.strategy_version}`}>
                  <td className="mono">{r.strategy}</td>
                  <td className="mono">{r.strategy_version}</td>
                  <td className="mono r">{r.fills}</td>
                  <td className={`mono r ${pnlClass(r.realized)}`}>{fmtUsd(r.realized, { sign: true })}</td>
                  <td className={`mono r ${pnlClass(r.unrealized)}`}>{fmtUsd(r.unrealized, { sign: true })}</td>
                  <td className={`mono r ${pnlClass(r.total)}`}>{fmtUsd(r.total, { sign: true })}</td>
                  <td className="mono r">{fmtUsd(r.fees)}</td>
                  <td className="mono">{r.open_symbols || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">No fills yet — attribution appears after the first trade.</div>
        )}
      </div>
    </div>
  );
}
