"""EMA pullback at the US cash open - the highest-accuracy gold pattern found so far (15m bars).

Found by a 286,790-combination sweep of a year of GC 1-minute data (Oct 2025 - Oct 2026), costs
included, validated quarter by quarter:

  * Trend filter: the fast EMA is above the slow EMA and price is above the slow EMA (mirrored
    for shorts). The slow EMA is the trend; the fast EMA is where pullbacks end.
  * Entry: the bar's low reaches down to the fast EMA (within ``touch`` x ATR) and the bar still
    closes back above it - the pullback was bought. Mirror for shorts.
  * Stop ``stop_atr`` x ATR, target ``rr`` x that risk. Wins often, each win roughly the size of
    each loss, so the edge is the win rate staying well above 50%.

Why only 8:20-10:00 CT: a pullback needs a trend to pull back into. Measured over the same year,
the identical rules scored PF 2.10 inside that window and 0.4-1.2 in every other hour of the day
(all-day: PF 1.03). The 8:30 CT cash open is when gold actually trends; the rest of the day it
chops and the pullback just keeps going.

Measured on 8:20-10:00 CT, 15m, EMA 21/100, stop 1.5 ATR, RR 1, flat at 13:00:
181 trades, 67% win, PF 2.03, +$10,903 on 1 MGC, worst drawdown $697, 11 of 13 months green.
Robust: every stop 1.0-2.5 ATR x RR 0.75-1.5 variant scores PF 1.47-2.16, and it still makes
PF 1.87 if the round-trip cost is 2.0 points instead of 0.35.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import indicators as ta
from ..bars import Bar, day, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy, clock, in_window, now_hhmm


@register
class EmaPullback(Strategy):
    name = "ema_pullback"
    description = "Buy the pullback to the fast EMA while the trend holds, US cash open only (high win rate)"

    @dataclass
    class Params:
        fast: int = 21  # the EMA pullbacks end at
        slow: int = 100  # the trend EMA
        touch: float = 0.0  # how close to the fast EMA the low must get, in ATR (0 = must reach it)
        confirm: bool = False  # also require the signal bar to close up (long) / down (short)
        stop_atr: float = 1.5
        rr: float = 1.0  # target = rr x risk
        session_start: int = 820
        session_end: int = 1000
        do_long: bool = True
        do_short: bool = True
        atr_len: int = 14
        news_mode: str = "off"  # "off" | "skip_7_8" | "skip_session"
        skip_bars: str = ""

    #: close anything still open at the next open after 13:00 CT - keeps every trade intraday
    flat_hhmm = 1300

    def _in_session(self, t: int) -> bool:
        return in_window(t, self.p.session_start, self.p.session_end)

    def _emas(self, bars: list[Bar]) -> tuple[list[float | None], list[float | None]]:
        c = [b.close for b in bars]
        return ta.ema(c, self.p.fast), ta.ema(c, self.p.slow)

    def signals(self, bars: list[Bar], ctx: Context) -> list[Signal | None]:
        p = self.p
        n = len(bars)
        out: list[Signal | None] = [None] * n
        if n < p.slow + p.atr_len:
            return out
        h = [b.high for b in bars]
        lo = [b.low for b in bars]
        c = [b.close for b in bars]
        o = [b.open for b in bars]
        atr = ta.atr(h, lo, c, p.atr_len)
        fast, slow = self._emas(bars)
        skip = {int(x) for x in str(p.skip_bars).replace(" ", "").split(",") if x}
        for i in range(n):
            a = atr[i]
            f, s = fast[i], slow[i]
            if a is None or a <= 0 or f is None or s is None:
                continue
            t = hhmm(bars[i].time)
            if not self._in_session(t) or t in skip:
                continue
            news = ctx.news_days.get(day(bars[i].time))
            if news and (p.news_mode == "skip_session" or (p.news_mode == "skip_7_8" and 700 <= t < 800)):
                continue
            risk = p.stop_atr * a
            up = c[i] > s and f > s
            dn = c[i] < s and f < s
            if p.do_long and up and lo[i] <= f + p.touch * a and c[i] > f and (c[i] > o[i] or not p.confirm):
                out[i] = Signal(
                    1,
                    stop=c[i] - risk,
                    target=c[i] + p.rr * risk,
                    info={"setup": f"pullback to the {p.fast} EMA ({f:,.1f}) held, uptrend above the {p.slow} EMA"},
                )
            elif p.do_short and dn and h[i] >= f - p.touch * a and c[i] < f and (c[i] < o[i] or not p.confirm):
                out[i] = Signal(
                    -1,
                    stop=c[i] + risk,
                    target=c[i] - p.rr * risk,
                    info={"setup": f"pullback to the {p.fast} EMA ({f:,.1f}) failed, downtrend below the {p.slow} EMA"},
                )
        return out

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        p = self.p
        if len(bars) < p.slow + p.atr_len:
            return []
        h = [b.high for b in bars]
        lo = [b.low for b in bars]
        c = [b.close for b in bars]
        a = ta.atr(h, lo, c, p.atr_len)[-1] or 0
        fast, slow = self._emas(bars)
        f, s = fast[-1] or 0, slow[-1] or 0
        up = c[-1] > s and f > s
        dn = c[-1] < s and f < s
        trend = "up" if up else "down" if dn else "none"
        return [
            ("Trend", f"{trend} ({p.fast} EMA {f:,.1f} vs {p.slow} EMA {s:,.1f})", trend != "none"),
            ("Pullback line", f"{f:,.1f} (needs a touch within {p.touch:g} ATR = {p.touch * a:,.1f})", None),
            ("Risk", f"{p.stop_atr:g} ATR = {p.stop_atr * a:,.1f} pts, target {p.rr:g}R = {p.rr * p.stop_atr * a:,.1f} pts", None),
        ]


def _ema_pb_summary(self, bars, ctx, live=None):
    p = self.p
    if len(bars) < p.slow + p.atr_len:
        return []
    win = f"{clock(p.session_start)}–{clock(p.session_end)} CT"
    if not in_window(now_hhmm(), p.session_start, p.session_end):
        return [("What to do", f"Nothing. This strategy only trades {win}.", False)]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    c = [b.close for b in bars]
    a = ta.atr(h, lo, c, p.atr_len)[-1] or 0
    fast, slow = self._emas(bars)
    f, s = fast[-1] or 0, slow[-1] or 0
    now = live.close if live is not None else c[-1]
    mins = int((bars[-1].time - bars[-2].time) / 60000) if len(bars) > 1 else 15
    up = now > s and f > s
    dn = now < s and f < s
    touch = p.touch * a
    if up:
        rows = [("What to do", f"Get ready to BUY. Gold is in an uptrend. A BUY alert comes if a {mins}-min bar dips to {f + touch:,.1f} and still closes above {f:,.1f}.", True)]
    elif dn:
        rows = [("What to do", f"Get ready to SELL. Gold is in a downtrend. A SELL alert comes if a {mins}-min bar pops to {f - touch:,.1f} and still closes below {f:,.1f}.", True)]
    else:
        rows = [("What to do", f"Nothing yet. No trend: gold needs to settle above or below its {p.slow} EMA ({s:,.1f}) first.", False)]
    if p.do_long:
        rows.append(("BUY alert comes if", f"gold is above {s:,.1f}, then a {mins}-min bar dips to {f + touch:,.1f} and closes back above {f:,.1f} (gold is {now:,.1f} now)", up))
    if p.do_short:
        rows.append(("SELL alert comes if", f"gold is below {s:,.1f}, then a {mins}-min bar pops to {f - touch:,.1f} and closes back below {f:,.1f} (gold is {now:,.1f} now)", dn))
    rows.append(("Close by", f"{clock(self.flat_hhmm)} CT - nothing is held overnight", False))
    return rows


EmaPullback.summary = _ema_pb_summary


def _ema_pb_levels(self, bars, ctx):
    p = self.p
    if len(bars) < p.slow + p.atr_len:
        return []
    fast, slow = self._emas(bars)
    return [
        (f"EMA {p.fast}", fast, "series"),
        (f"EMA {p.slow}", slow, "series"),
        ("Pullback", fast[-1] or 0, "trigger"),
    ]


EmaPullback.levels = _ema_pb_levels
