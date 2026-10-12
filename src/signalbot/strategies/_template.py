"""TEMPLATE for a new strategy - copy this file, rename it (no leading underscore), edit.

Steps:
1. cp _template.py my_strategy.py   (files starting with "_" are not loaded)
2. change ``name``, ``description``, ``Params`` and the rules in ``signals``
3. add a [[watch]] block with strategy = "my_strategy" to config.toml
4. signalbot backtest --watch <watch name>   to see how it would have done

Rules of the game:
* ``signals`` returns one item per bar: None, or Signal(direction, stop, target).
* Bar i's decision may only use bars[0..i] (no peeking at later bars).
* The engine fills at the next bar's open, manages stop/target, and keeps one trade at a time.
* Set ``flat_hhmm`` (class attribute or in __init__) for intraday strategies that must be
  flat by a clock time.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import indicators as ta
from ..bars import Bar, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy


@register
class EmaCrossExample(Strategy):
    name = "ema_cross_example"
    description = "Example only: EMA 9/21 cross during a session window"

    @dataclass
    class Params:
        fast: int = 9
        slow: int = 21
        session_start: int = 830
        session_end: int = 1100
        atr_mult: float = 1.5
        rr: float = 1.5

    def signals(self, bars: list[Bar], ctx: Context) -> list[Signal | None]:
        p = self.p
        c = [b.close for b in bars]
        fast, slow = ta.ema(c, p.fast), ta.ema(c, p.slow)
        atr = ta.atr([b.high for b in bars], [b.low for b in bars], c, 14)
        out: list[Signal | None] = []
        for i, b in enumerate(bars):
            out.append(None)
            if i == 0 or None in (fast[i], slow[i], fast[i - 1], slow[i - 1], atr[i]):
                continue
            if not (p.session_start <= hhmm(b.time) < p.session_end):
                continue
            risk = p.atr_mult * atr[i]
            if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
                out[i] = Signal(1, stop=b.close - risk, target=b.close + p.rr * risk)
            elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
                out[i] = Signal(-1, stop=b.close + risk, target=b.close - p.rr * risk)
        return out

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        return []
