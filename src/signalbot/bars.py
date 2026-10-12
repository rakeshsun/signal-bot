"""Candles and time helpers shared by every strategy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class Bar:
    """One closed candle. ``time`` is the bar's OPEN time in epoch milliseconds (UTC)."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.time / 1000, tz=CHICAGO)


def hhmm(ts_ms: int, tz: ZoneInfo = CHICAGO) -> int:
    """Clock time as an int, e.g. 7:30 -> 730."""
    d = datetime.fromtimestamp(ts_ms / 1000, tz=tz)
    return d.hour * 100 + d.minute


def day(ts_ms: int, tz: ZoneInfo = CHICAGO) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=tz).date()


def interval_ms(interval: str) -> int:
    """'15m' -> 900000. Supports s, m, h, d."""
    m = re.fullmatch(r"(\d+)([smhd])", interval.strip())
    if not m:
        raise ValueError(f"bad interval {interval!r}; use e.g. '5m', '15m', '1h'")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * 1000
