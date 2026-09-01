"use client";

import { fmtUsd, pnlClass, useLiveData } from "../../lib/api";

export default function Attribution() {
  const { data: rows } = useLiveData("/attribution", {
    intervalMs: 15000,
    eventTypes: ["Fill"],
  });

  return (
    <div className="panel">
      <h2>P&amp;L attribution by strategy version</h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
        Realized cashflow per strategy version (sells − buys − fees). Open positions show as negative
        cashflow until they are sold — pair with the unrealized P&amp;L on Positions.
      </p>
      <div className="tablewrap">
        {rows?.length ? (
          <table>
            <thead>
              <tr>
                <th>Strategy</th><th>Version</th>
                <th className="r">Fills</th><th className="r">Fees</th>
                <th className="r">Net cashflow</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.strategy}-${r.strategy_version}`}>
                  <td className="mono">{r.strategy}</td>
                  <td className="mono">{r.strategy_version}</td>
                  <td className="mono r">{r.fills}</td>
                  <td className="mono r">{fmtUsd(r.fees)}</td>
                  <td className={`mono r ${pnlClass(r.cashflow)}`}>{fmtUsd(r.cashflow, { sign: true })}</td>
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
