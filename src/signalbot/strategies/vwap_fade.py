"""VWAP stretch fade - high win-rate mean reversion for gold during US morning hours (15m bars).

Found by mining a year of GC data (ideas picked on Oct 2025 - May 2026, then checked on the
unseen Jun - Oct 2026 months):
  * Session VWAP is anchored at the CME open (17:00 CT) and uses bar volume.
  * When a bar closes more than ``stretch_atr`` x ATR away from VWAP AND the bar itself turns
    back toward VWAP (red candle above VWAP -> short, green candle below VWAP -> long), fade it.
  * Small target (``target_atr``), wider stop (``stop_atr``): wins often, each win is smaller
    than each loss. The edge is the win rate staying above the break-even rate (stop / (stop+target)).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .. import indicators as ta
from ..bars import CHICAGO, Bar, day, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy, clock, in_window, now_hhmm


def _cme_day(ts_ms: int) -> int:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=CHICAGO)
    if d.hour >= 17:
        d += timedelta(days=1)
    return d.toordinal()


def session_vwap(bars: list[Bar]) -> list[float]:
    out, cur, pv, vol = [], None, 0.0, 0.0
    for b in bars:
        d = _cme_day(b.time)
        if d != cur:
            cur, pv, vol = d, 0.0, 0.0
        tp = (b.high + b.low + b.close) / 3
        v = b.volume if b.volume > 0 else 1.0
        pv, vol = pv + tp * v, vol + v
        out.append(pv / vol)
    return out


@register
class VwapFade(Strategy):
    name = "vwap_fade"
    description = "Fade gold when it stretches far from session VWAP and turns back (high win rate, small target)"

    @dataclass
    class Params:
        stretch_atr: float = 3.0  # how far from VWAP (in ATR) counts as stretched
        stop_atr: float = 1.5
        target_atr: float = 1.0
        confirm: bool = True  # the signal bar must turn back toward VWAP
        session_start: int = 730
        session_end: int = 1100
        do_long: bool = True
        do_short: bool = True
        news_mode: str = "off"  # "off" | "skip_7_8" | "skip_session"
        skip_bars: str = ""

    def _in_session(self, t: int) -> bool:
        a, b = self.p.session_start, self.p.session_end
        return (a <= t < b) if a <= b else (t >= a or t < b)

    def signals(self, bars: list[Bar], ctx: Context) -> list[Signal | None]:
        p = self.p
        n = len(bars)
        out: list[Signal | None] = [None] * n
        if n < 30:
            return out
        h = [b.high for b in bars]
        lo = [b.low for b in bars]
        c = [b.close for b in bars]
        o = [b.open for b in bars]
        atr = ta.atr(h, lo, c, 14)
        vw = session_vwap(bars)
        skip = {int(x) for x in str(p.skip_bars).replace(" ", "").split(",") if x}
        for i in range(n):
            a = atr[i]
            if a is None or a <= 0:
                continue
            t = hhmm(bars[i].time)
            if not self._in_session(t) or t in skip:
                continue
            news = ctx.news_days.get(day(bars[i].time))
            if news and (p.news_mode == "skip_session" or (p.news_mode == "skip_7_8" and 700 <= t < 800)):
                continue
            stretch = (c[i] - vw[i]) / a
            if p.do_short and stretch >= p.stretch_atr and (c[i] < o[i] or not p.confirm):
                out[i] = Signal(-1, stop=c[i] + p.stop_atr * a, target=c[i] - p.target_atr * a,
                                info={"setup": f"{stretch:.1f} ATR above VWAP, turning down"})
            elif p.do_long and stretch <= -p.stretch_atr and (c[i] > o[i] or not p.confirm):
                out[i] = Signal(1, stop=c[i] - p.stop_atr * a, target=c[i] + p.target_atr * a,
                                info={"setup": f"{-stretch:.1f} ATR below VWAP, turning up"})
        return out

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        if len(bars) < 30:
            return []
        p = self.p
        h = [b.high for b in bars]
        lo = [b.low for b in bars]
        c = [b.close for b in bars]
        a = ta.atr(h, lo, c, 14)[-1] or 0
        vw = session_vwap(bars)[-1]
        stretch = (c[-1] - vw) / a if a else 0
        far = abs(stretch) >= p.stretch_atr
        return [
            ("Session VWAP", f"{vw:,.1f}", None),
            ("Distance", f"{stretch:+.1f} ATR (needs ±{p.stretch_atr:g})", far),
            ("Fade zone", f"above {vw + p.stretch_atr * a:,.1f} / below {vw - p.stretch_atr * a:,.1f}", None),
        ]


def _vwap_summary(self, bars, ctx, live=None):
    p = self.p
    if len(bars) < 30:
        return []
    win = f"{clock(p.session_start)}–{clock(p.session_end)} CT"
    if not in_window(now_hhmm(), p.session_start, p.session_end):
        return [("What to do", f"Nothing. This strategy only trades {win}.", False)]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    c = [b.close for b in bars]
    a = ta.atr(h, lo, c, 14)[-1] or 0
    vw = session_vwap(bars + ([live] if live is not None else []))[-1]
    now = live.close if live is not None else c[-1]
    up, dn = vw + p.stretch_atr * a, vw - p.stretch_atr * a
    mins = int((bars[-1].time - bars[-2].time) / 60000)
    if now >= up:
        rows = [("What to do", f"Get ready. Gold is stretched high: a SELL alert comes if a {mins}-min bar closes red (down).", True)]
    elif now <= dn:
        rows = [("What to do", f"Get ready. Gold is stretched low: a BUY alert comes if a {mins}-min bar closes green (up).", True)]
    else:
        rows = [("What to do", "Nothing yet. Just wait.", False)]
    if p.do_long:
        rows.append(("BUY alert comes if", f"gold drops below {dn:,.1f}, then a {mins}-min bar closes green (gold is {now:,.1f} now)", now <= dn))
    if p.do_short:
        rows.append(("SELL alert comes if", f"gold rises above {up:,.1f}, then a {mins}-min bar closes red (gold is {now:,.1f} now)", now >= up))
    return rows


VwapFade.summary = _vwap_summary


def _vwap_levels(self, bars, ctx):
    if len(bars) < 30:
        return []
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    c = [b.close for b in bars]
    a = ta.atr(h, lo, c, 14)[-1] or 0
    vw = session_vwap(bars)
    return [("VWAP", vw, "series"), ("Sell zone", vw[-1] + self.p.stretch_atr * a, "zone"),
            ("Buy zone", vw[-1] - self.p.stretch_atr * a, "zone")]


VwapFade.levels = _vwap_levels
