"use client";

import { useEffect, useMemo, useRef } from "react";
import { createChart } from "lightweight-charts";
import { fmtUsd, pnlClass, useLiveData } from "../lib/api";

function EquityChart({ curve }) {
  const holder = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  useEffect(() => {
    if (!holder.current) return;
    const style = getComputedStyle(document.documentElement);
    const chart = createChart(holder.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: style.getPropertyValue("--muted").trim(),
        fontFamily: style.getPropertyValue("--mono").trim(),
        fontSize: 11,
      },
      grid: {
        vertLines: { color: style.getPropertyValue("--line").trim() },
        horzLines: { color: style.getPropertyValue("--line").trim() },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
    });
    const series = chart.addAreaSeries({
      lineColor: style.getPropertyValue("--accent").trim(),
      topColor: "rgba(62, 143, 214, 0.25)",
      bottomColor: "rgba(62, 143, 214, 0.02)",
      lineWidth: 2,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !curve?.length) return;
    const seen = new Set();
    const points = [];
    for (const p of curve) {
      const t = Math.floor(new Date(p.ts).getTime() / 1000);
      if (!seen.has(t)) {
        seen.add(t);
        points.push({ time: t, value: p.equity });
      }
    }
    seriesRef.current.setData(points);
    chartRef.current?.timeScale().fitContent();
  }, [curve]);

  return <div className="chart-holder" ref={holder} />;
}

export default function Overview() {
  const { data: status } = useLiveData("/status", {
    intervalMs: 5000,
    eventTypes: ["EquitySnapshot", "Fill", "HaltEvent", "ResumeEvent"],
  });
  const { data: curve } = useLiveData("/equity?hours=2160", {
    intervalMs: 15000,
    eventTypes: ["EquitySnapshot"],
  });

  const drawdown = useMemo(() => {
    if (!curve?.length) return null;
    let peak = -Infinity;
    let maxDd = 0;
    for (const p of curve) {
      peak = Math.max(peak, p.equity);
      if (peak > 0) maxDd = Math.max(maxDd, (peak - p.equity) / peak);
    }
    const last = curve[curve.length - 1].equity;
    const current = peak > 0 ? (peak - last) / peak : 0;
    return { current, max: maxDd };
  }, [curve]);

  const pnlToday = status ? status.realized_pnl_today + status.unrealized_pnl : null;

  return (
    <>
      <div className="tiles">
        <div className="tile">
          <div className="label">Equity</div>
          <div className="value">{fmtUsd(status?.equity)}</div>
          <div className="sub">cash {fmtUsd(status?.cash)}</div>
        </div>
        <div className="tile">
          <div className="label">P&amp;L today</div>
          <div className={`value ${pnlClass(pnlToday)}`}>{fmtUsd(pnlToday, { sign: true })}</div>
          <div className="sub">realized {fmtUsd(status?.realized_pnl_today, { sign: true })}</div>
        </div>
        <div className="tile">
          <div className="label">Unrealized</div>
          <div className={`value ${pnlClass(status?.unrealized_pnl)}`}>
            {fmtUsd(status?.unrealized_pnl, { sign: true })}
          </div>
          <div className="sub">{status?.open_positions ?? "—"} open position(s)</div>
        </div>
        <div className="tile">
          <div className="label">Gross exposure</div>
          <div className="value">{fmtUsd(status?.gross_exposure)}</div>
          <div className="sub">
            {status?.equity ? `${((status.gross_exposure / status.equity) * 100).toFixed(0)}% of equity` : "—"}
          </div>
        </div>
        <div className="tile">
          <div className="label">Drawdown</div>
          <div className={`value ${drawdown?.current > 0.01 ? "neg" : ""}`}>
            {drawdown ? `${(drawdown.current * 100).toFixed(1)}%` : "—"}
          </div>
          <div className="sub">max {drawdown ? `${(drawdown.max * 100).toFixed(1)}%` : "—"}</div>
        </div>
      </div>

      <div className="panel">
        <h2>Equity curve</h2>
        {curve?.length ? <EquityChart curve={curve} /> : <div className="empty">No equity snapshots yet — start the engine.</div>}
      </div>
    </>
  );
}
