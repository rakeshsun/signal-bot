"""Bars from a CSV file: columns time, open, high, low, close, volume.

``time`` may be epoch seconds or an ISO timestamp (bar open time). A TradingView
"Export chart data" CSV works as-is.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from ..bars import Bar


def load(path: Path) -> list[Bar]:
    bars: list[Bar] = []
    with path.open() as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): v for k, v in row.items()}
            t = row["time"].strip()
            if t.replace(".", "", 1).isdigit():
                ms = int(float(t) * 1000)
            else:
                ms = int(datetime.fromisoformat(t).timestamp() * 1000)
            bars.append(
                Bar(ms, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row.get("volume") or 0))
            )
    return sorted(bars, key=lambda b: b.time)
