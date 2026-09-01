"""Generate a synthetic daily-bars CSV for replay mode.

    uv run python scripts/make_replay_csv.py [days] [out.csv]

Deterministic (seeded) geometric random walks per universe symbol — enough to
exercise the full pipeline and light up the dashboard without market data keys.
"""

import csv
import math
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from driftline.strategy.baseline_momentum import UNIVERSE  # noqa: E402


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    out = sys.argv[2] if len(sys.argv) > 2 else "replay-bars.csv"

    rng = random.Random(7)
    drifts = {s: rng.uniform(-0.0004, 0.0012) for s in UNIVERSE}
    vols = {s: rng.uniform(0.008, 0.016) for s in UNIVERSE}
    prices = {s: rng.uniform(50, 500) for s in UNIVERSE}

    start = date.today() - timedelta(days=days)
    rows = []
    d = start
    while d <= date.today():
        if d.weekday() < 5:
            for s in UNIVERSE:
                ret = drifts[s] + rng.gauss(0, vols[s])
                o = prices[s]
                prices[s] *= math.exp(ret)
                c = prices[s]
                rows.append({
                    "date": d.isoformat(), "symbol": s,
                    "open": round(o, 2), "high": round(max(o, c) * 1.004, 2),
                    "low": round(min(o, c) * 0.996, 2), "close": round(c, 2),
                    "volume": rng.randint(1_000_000, 20_000_000),
                })
        d += timedelta(days=1)

    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} bars for {len(UNIVERSE)} symbols to {out}")


if __name__ == "__main__":
    main()
