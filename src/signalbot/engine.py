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
* optional runner (``Signal.runner`` = fraction kept, e.g. 0.5): when the target is hit only
  the rest of the position closes there; the runner's stop moves to the entry price and it
  exits on a change of character (CHoCH): a close beyond the latest swing low (long) / swing
  high (short) that formed after the entry. Swings come from ``structure`` (see simulate).
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
    runner: float = 0.0  # fraction of the position kept after the target (0 = all exits at target)


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
    exit_reason: str | None = None  # "target" | "stop" | "time" | "choch"
    runner: float = 0.0
    part_index: int | None = None  # bar where the target took the first part off
    part_time: int | None = None
    part_price: float | None = None
    swing: float | None = None  # runner: latest swing after entry (a close beyond it = CHoCH exit)

    @property
    def active_stop(self) -> float:
        """Where the stop is now: the entry price once the first part is off, else the initial stop."""
        return self.entry_price if self.part_price is not None and self.entry_price is not None else self.stop

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
        """Points per contract, averaged over the whole position (first part + runner)."""
        if self.exit_price is None or self.entry_price is None:
            return None
        rest = (self.exit_price - self.entry_price) * self.direction
        if self.part_price is None:
            return rest
        first = (self.part_price - self.entry_price) * self.direction
        return (1 - self.runner) * first + self.runner * rest

    @property
    def won(self) -> bool | None:
        pts = self.points
        return None if pts is None else pts > 0


@dataclass
class Event:
    kind: str  # "entry" | "partial" | "exit"
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


def simulate(bars: list[Bar], signals: list[Signal | None], flat_hhmm: int | None = None, structure=None) -> Result:
    """Turn per-bar signals into trades. ``signals[i]`` is evaluated on bar i's close.
    ``structure`` = (swing_highs, swing_lows, right_bars): the swing value confirmed at bar i (or
    None), printed ``right_bars`` bars earlier. Needed only for runners (CHoCH exits)."""
    far = float("inf")
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
                d = trade.direction
                running = trade.part_price is not None
                hit = first_touch(bar, trade.active_stop, (d * far) if running else trade.target, d)
                if hit is not None and hit[0] == "target" and trade.runner > 0 and not running:
                    # first part off at the target; the runner carries on with its stop at entry
                    trade.part_index, trade.part_time, trade.part_price = i, bar.time, hit[1]
                    res.events.append(Event("partial", i, bar.time, trade))
                    hit = None
                if hit is None and flat_hhmm is not None and hhmm(bar.time) >= flat_hhmm:
                    hit = ("time", bar.close)
                if hit is None and trade.runner > 0 and structure is not None:
                    highs, lows, right = structure
                    p = lows[i] if d > 0 else highs[i]
                    if p is not None and i - right >= trade.entry_index:
                        trade.swing = p  # a swing that formed after the entry
                    if trade.part_price is not None and trade.swing is not None and (bar.close - trade.swing) * d < 0:
                        hit = ("choch", bar.close)
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
            runner=sig.runner,
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
