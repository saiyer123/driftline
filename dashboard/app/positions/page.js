"use client";

import { fmtQty, fmtUsd, pnlClass, useLiveData } from "../../lib/api";

export default function Positions() {
  const { data: positions } = useLiveData("/positions", {
    intervalMs: 5000,
    eventTypes: ["Fill", "PositionSnapshot"],
  });

  return (
    <div className="panel">
      <h2>Open positions</h2>
      <div className="tablewrap">
        {positions?.length ? (
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="r">Qty</th>
                <th className="r">Avg entry</th>
                <th className="r">Mark</th>
                <th className="r">Market value</th>
                <th className="r">Unrealized P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.symbol}>
                  <td className="mono">{p.symbol}</td>
                  <td className="mono r">{fmtQty(p.qty)}</td>
                  <td className="mono r">{fmtUsd(p.avg_entry)}</td>
                  <td className="mono r">{fmtUsd(p.mark)}</td>
                  <td className="mono r">{fmtUsd(p.market_value)}</td>
                  <td className={`mono r ${pnlClass(p.unrealized_pnl)}`}>
                    {fmtUsd(p.unrealized_pnl, { sign: true })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">No open positions.</div>
        )}
      </div>
    </div>
  );
}
