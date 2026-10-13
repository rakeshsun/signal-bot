#!/usr/bin/env python3
"""Download CME futures history from Databento and save 15-minute bars for `signalbot backtest --csv`.

    pip install -e ".[data]"                 # databento + pandas
    export DATABENTO_API_KEY=db-xxxxxxxx      # or put it in .env
    python scripts/fetch_databento.py --root GC --days 365            # shows the cost, asks first
    signalbot backtest gold --csv data/GC_15m.csv

Uses the volume-based continuous front month (GC.v.0): the contract with the most volume each
day, like TradingView's GC1!. Prices jump a little at each roll (no back-adjustment), which can
nudge the indicators for a few bars around roll days.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="GC", help="futures root: GC (gold), MGC, ES, SI ...")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--interval", default="15min", help="bar size for the output, pandas style (15min, 5min, 1h)")
    ap.add_argument("--out", type=Path, help="output CSV (default data/<ROOT>_15m.csv)")
    ap.add_argument("--yes", action="store_true", help="don't ask before downloading")
    a = ap.parse_args()

    load_env()
    try:
        import databento as db
        import pandas as pd
    except ImportError:
        sys.exit('Missing packages. Run:  pip install -e ".[data]"')
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        sys.exit("Set DATABENTO_API_KEY (Databento portal -> API keys) in your shell or in .env")

    client = db.Historical(key)
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=1)
    start = end - timedelta(days=a.days)
    req = dict(dataset="GLBX.MDP3", schema="ohlcv-1m", symbols=[f"{a.root}.v.0"], stype_in="continuous",
               start=start.isoformat(), end=end.isoformat())

    cost = client.metadata.get_cost(**req)
    print(f"{a.root} 1-minute bars {start:%Y-%m-%d} -> {end:%Y-%m-%d}: estimated cost ${cost:.2f} "
          "(charged to your Databento credit)")
    if not a.yes and input("Download? [y/N] ").strip().lower() != "y":
        sys.exit("Cancelled.")

    df = client.timeseries.get_range(**req).to_df()
    if df.empty:
        sys.exit("No data returned.")
    df = df[["open", "high", "low", "close", "volume"]].sort_index()
    bars = df.resample(a.interval, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()

    out = a.out or ROOT / "data" / f"{a.root}_{a.interval.replace('min', 'm')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    bars.insert(0, "time", bars.index.asi8 // 1_000_000_000)
    bars.to_csv(out, index=False)
    print(f"Saved {len(bars):,} bars to {out}  ({bars.index[0]:%Y-%m-%d} -> {bars.index[-1]:%Y-%m-%d})")
    print(f"Next:  signalbot backtest gold --csv {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
