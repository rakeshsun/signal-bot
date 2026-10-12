"""ES Range Breakout - the S&P 500 futures strategy from the TradingView script.

Buys when a 15-minute bar closes above the day's high so far (range measured from the
8:30 CT open, at least 4 bars) on above-average volume, between 9:30 and 11:00 CT.
Stop 1.25 x ATR(14), target 1R, anything open is closed at 3:00 pm CT. Long-only by
default: the short side lost money in testing. Backtested on CME ES1! (Apr-Oct 2026):
about 66% wins but a thin edge (profit factor ~1.6) - paper trade it first.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import indicators as ta
from ..bars import Bar, day, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy


@register
class EsRangeBreakout(Strategy):
    name = "es_range_breakout"
    description = "ES 15m breakout of the day's high 9:30-11:00 CT, 1.25 ATR stop, 1R target, flat by 3pm"

    @dataclass
    class Params:
        open_hhmm: int = 830
        from_hhmm: int = 930
        to_hhmm: int = 1100
        min_bars: int = 4
        use_vol: bool = True
        do_long: bool = True
        do_short: bool = False
        atr_mult: float = 1.25
        rr: float = 1.0
        flat_hhmm: int = 1500

    def __init__(self, params=None) -> None:
        super().__init__(params)
        self.flat_hhmm = self.p.flat_hhmm  # engine closes open trades at this time

    def _state(self, bars: list[Bar]):
        p = self.p
        vol_avg = ta.sma([b.volume for b in bars], 20)
        atr = ta.atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 14)
        prior_hi: list[float | None] = []
        prior_lo: list[float | None] = []
        prior_n: list[int] = []
        n_bars: list[int] = []
        d_hi = d_lo = None
        d_day = None
        nb = 0
        for b in bars:
            t, dk = hhmm(b.time), day(b.time)
            ph, pl, pn = d_hi, d_lo, nb
            if p.open_hhmm <= t < p.flat_hhmm:
                if dk != d_day:
                    d_day, d_hi, d_lo, nb = dk, b.high, b.low, 1
                    ph = pl = None
                    pn = 0
                else:
                    d_hi, d_lo, nb = max(d_hi, b.high), min(d_lo, b.low), nb + 1
            prior_hi.append(ph)
            prior_lo.append(pl)
            prior_n.append(pn)
            n_bars.append(nb if d_day == dk and p.open_hhmm <= t < p.flat_hhmm else 0)
        return vol_avg, atr, prior_hi, prior_lo, prior_n, n_bars

    def signals(self, bars: list[Bar], ctx: Context) -> list[Signal | None]:
        p = self.p
        vol_avg, atr, ph, pl, pn, _ = self._state(bars)
        out: list[Signal | None] = []
        for i, b in enumerate(bars):
            out.append(None)
            if i == 0 or atr[i] is None or not atr[i]:
                continue
            t = hhmm(b.time)
            if not (p.from_hhmm <= t < p.to_hhmm):
                continue
            vol_ok = (not p.use_vol) or (vol_avg[i] is not None and b.volume > vol_avg[i])
            prev = bars[i - 1]
            brk_l = (
                ph[i] is not None and pn[i] >= p.min_bars and b.close > ph[i]
                and ph[i - 1] is not None and prev.close <= ph[i - 1] and vol_ok
            )
            brk_s = (
                pl[i] is not None and pn[i] >= p.min_bars and b.close < pl[i]
                and pl[i - 1] is not None and prev.close >= pl[i - 1] and vol_ok
            )
            risk = p.atr_mult * atr[i]
            if p.do_long and brk_l:
                out[i] = Signal(1, stop=b.close - risk, target=b.close + p.rr * risk, info={"setup": "day-high breakout"})
            elif p.do_short and brk_s:
                out[i] = Signal(-1, stop=b.close + risk, target=b.close - p.rr * risk, info={"setup": "day-low breakdown"})
        return out

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        if not bars:
            return []
        p = self.p
        vol_avg, _, _, _, _, n_bars = self._state(bars)
        b = bars[-1]
        t = hhmm(b.time)
        in_win = p.from_hhmm <= t < p.to_hhmm
        vol_ok = vol_avg[-1] is not None and b.volume > vol_avg[-1]
        return [
            ("Entry window 9:30-11:00", "open" if in_win else "closed", None),
            (f"Range bars (need {p.min_bars})", str(n_bars[-1]), None),
            ("Volume > 20-bar avg", vol_ok, None),
        ]
