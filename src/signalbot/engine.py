"""Generic trade simulation shared by every strategy.

A strategy only says *where it would enter*: for each closed bar, either no
signal or a :class:`Signal` with direction, stop and target. The engine then
applies the same execution rules as a TradingView ``strategy()`` script:

* the entry fills at the next bar's open;
* stop and target are live from the fill bar onward; a gap through a level
  fills at the open; when one bar touches both, TradingView's bar-path rule
  decides (open -> high -> low -> close if the high is nearer the open,
  otherwise open -> low -> high -> close);
* one trade at a time: signals are ignored while a trade is open, and (like a
  Pine script that tracks an ``active`` flag) also on the bar where a trade
  that filled on the previous bar closes;
* optional ``flat_hhmm``: an open trade is closed at the close of the first bar
  starting at or after that clock time (Pine's ``strategy.close_all`` fills at the
  next open, a tick later; closing on the bar close lets the alert go out on time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bars import Bar, hhmm


@dataclass
class Signal:
    direction: int  # +1 long, -1 short
    stop: float
    target: float
    info: dict[str, Any] = field(default_factory=dict)  # extra text for alerts


@dataclass
class Trade:
    direction: int
    signal_index: int
    signal_time: int
    signal_close: float
    stop: float
    target: float
    info: dict[str, Any] = field(default_factory=dict)
    entry_index: int | None = None
    entry_price: float | None = None
    exit_index: int | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "target" | "stop" | "time"

    @property
    def risk(self) -> float:
        return abs(self.signal_close - self.stop)

    @property
    def reward(self) -> float:
        return abs(self.target - self.signal_close)

    @property
    def is_open(self) -> bool:
        return self.exit_index is None

    @property
    def points(self) -> float | None:
        if self.exit_price is None or self.entry_price is None:
            return None
        return (self.exit_price - self.entry_price) * self.direction

    @property
    def won(self) -> bool | None:
        pts = self.points
        return None if pts is None else pts > 0


@dataclass
class Event:
    kind: str  # "entry" | "exit"
    bar_index: int
    bar_time: int
    trade: Trade


@dataclass
class Result:
    bars: list[Bar]
    trades: list[Trade] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    def win_rate(self, direction: int | None = None) -> tuple[int, int]:
        closed = [t for t in self.trades if not t.is_open and (direction is None or t.direction == direction)]
        return sum(1 for t in closed if t.won), len(closed)

    @property
    def open_trade(self) -> Trade | None:
        return next((t for t in reversed(self.trades) if t.is_open), None)


def first_touch(bar: Bar, stop: float, target: float, direction: int) -> tuple[str, float] | None:
    """Which of stop/target the bar reached first (TradingView broker-emulator rules)."""
    if direction > 0:
        if bar.open <= stop:
            return "stop", bar.open
        if bar.open >= target:
            return "target", bar.open
        hit_s, hit_t = bar.low <= stop, bar.high >= target
    else:
        if bar.open >= stop:
            return "stop", bar.open
        if bar.open <= target:
            return "target", bar.open
        hit_s, hit_t = bar.high >= stop, bar.low <= target
    if not hit_s and not hit_t:
        return None
    if hit_s != hit_t:
        return ("stop", stop) if hit_s else ("target", target)
    high_first = (bar.high - bar.open) <= (bar.open - bar.low)
    if direction > 0:
        return ("target", target) if high_first else ("stop", stop)
    return ("stop", stop) if high_first else ("target", target)


def simulate(bars: list[Bar], signals: list[Signal | None], flat_hhmm: int | None = None) -> Result:
    """Turn per-bar signals into trades. ``signals[i]`` is evaluated on bar i's close."""
    if len(signals) != len(bars):
        raise ValueError("signals must have one entry per bar")
    res = Result(bars=bars)
    trade: Trade | None = None

    for i, bar in enumerate(bars):
        if trade is not None and trade.is_open:
            # entry fill at this bar's open
            if trade.entry_index is None and i == trade.signal_index + 1:
                trade.entry_index, trade.entry_price = i, bar.open
            if trade.entry_index is not None:
                hit = first_touch(bar, trade.stop, trade.target, trade.direction)
                if hit is None and flat_hhmm is not None and hhmm(bar.time) >= flat_hhmm:
                    hit = ("time", bar.close)
                if hit is not None:
                    trade.exit_reason, trade.exit_price = hit
                    trade.exit_index, trade.exit_time = i, bar.time
                    res.events.append(Event("exit", i, bar.time, trade))

        # the "active" flag clears once flat and past the fill bar
        if trade is not None and not trade.is_open and i > trade.signal_index + 1:
            trade = None

        sig = signals[i]
        if trade is not None or sig is None:
            continue
        trade = Trade(
            direction=sig.direction,
            signal_index=i,
            signal_time=bar.time,
            signal_close=bar.close,
            stop=sig.stop,
            target=sig.target,
            info=dict(sig.info),
        )
        res.trades.append(trade)
        res.events.append(Event("entry", i, bar.time, trade))
    return res
