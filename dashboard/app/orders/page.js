"use client";

import { useState } from "react";
import { fmtQty, fmtTime, fmtUsd, useLiveData } from "../../lib/api";

export default function Orders() {
  const [tab, setTab] = useState("orders");
  const { data: orders } = useLiveData("/orders", {
    intervalMs: 8000,
    eventTypes: ["OrderUpdate"],
  });
  const { data: fills } = useLiveData("/fills", {
    intervalMs: 8000,
    eventTypes: ["Fill"],
  });

  return (
    <div className="panel">
      <div className="tabs">
        <button className={`tab${tab === "orders" ? " active" : ""}`} onClick={() => setTab("orders")}>
          Orders
        </button>
        <button className={`tab${tab === "fills" ? " active" : ""}`} onClick={() => setTab("fills")}>
          Fills
        </button>
      </div>

      {tab === "orders" ? (
        <div className="tablewrap">
          {orders?.length ? (
            <table>
              <thead>
                <tr>
                  <th>Time</th><th>Symbol</th><th>Side</th>
                  <th className="r">Qty</th><th>Status</th><th>Strategy</th><th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o, i) => (
                  <tr key={`${o.intent_id}-${o.status}-${i}`}>
                    <td className="mono">{fmtTime(o.ts)}</td>
                    <td className="mono">{o.symbol}</td>
                    <td><span className={`tag ${o.side}`}>{o.side.toUpperCase()}</span></td>
                    <td className="mono r">{fmtQty(o.qty)}</td>
                    <td><span className={`tag ${o.status}`}>{o.status.replace("_", " ")}</span></td>
                    <td className="mono">{o.strategy}@{o.strategy_version}</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 380 }}>{o.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">No orders yet.</div>
          )}
        </div>
      ) : (
        <div className="tablewrap">
          {fills?.length ? (
            <table>
              <thead>
                <tr>
                  <th>Time</th><th>Symbol</th><th>Side</th>
                  <th className="r">Qty</th><th className="r">Price</th>
                  <th className="r">Value</th><th className="r">Fee</th><th>Strategy</th>
                </tr>
              </thead>
              <tbody>
                {fills.map((f, i) => (
                  <tr key={`${f.intent_id}-${i}`}>
                    <td className="mono">{fmtTime(f.ts)}</td>
                    <td className="mono">{f.symbol}</td>
                    <td><span className={`tag ${f.side}`}>{f.side.toUpperCase()}</span></td>
                    <td className="mono r">{fmtQty(f.qty)}</td>
                    <td className="mono r">{fmtUsd(f.price)}</td>
                    <td className="mono r">{fmtUsd(f.qty * f.price)}</td>
                    <td className="mono r">{fmtUsd(f.fee)}</td>
                    <td className="mono">{f.strategy}@{f.strategy_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">No fills yet.</div>
          )}
        </div>
      )}
    </div>
  );
}
