"""Levels + Fair Value Gaps - liquidity-level and FVG setups (ICT style), 15-minute bars.

Key levels (from completed sessions only, never peeking ahead):
  * PDH / PDL - prior trading day's high / low (CME day 17:00-16:00 CT)
  * ONH / ONL - overnight high / low (17:00 CT until the entry window opens)

Fair value gap (3-bar imbalance): bullish when low[i] > high[i-2] (gap = high[i-2]..low[i]),
bearish when high[i] < low[i-2].

Setups (``mode``):
  * "sweep_fvg"    - price trades through a level (takes the stops beyond it), closes back on the
                     other side, then within a few bars a gap forms in the reversal direction:
                     enter on that bar's close. Stop beyond the sweep extreme.
  * "fvg_retest"   - with the trend (EMA 20 > 50, price above EMA 200 for longs), price comes back
                     into an untouched gap from the last N bars and closes back out of it in the
                     trend direction. Stop beyond the far side of the gap.
  * "level_fvg"    - combined: a gap that formed AT a key level (within level_near_atr x ATR)
                     is retested - price dips back into it and closes out the far side. Longs use
                     gaps at support (PDL/ONL), shorts gaps at resistance (PDH/ONH). Stop beyond
                     the far side of the gap or the level, whichever is further.
  * "level_reject" - a bar pokes through a key level and closes back with a long wick (close in
                     the outer third of its range). Stop beyond the wick.
Target = rr x risk. Trades whose stop would be wider than max_risk_atr x ATR are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import indicators as ta
from ..bars import Bar, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy


def _trading_day(ts_ms: int) -> int:
    """CME trading day id: bars from 17:00 CT belong to the next day."""
    from datetime import datetime, timedelta

    from ..bars import CHICAGO

    d = datetime.fromtimestamp(ts_ms / 1000, tz=CHICAGO)
    if d.hour >= 17:
        d = d + timedelta(days=1)
    return d.year * 10000 + d.month * 100 + d.day


@register
class LevelsFvg(Strategy):
    name = "levels_fvg"
    description = "Liquidity levels (prior-day / overnight high-low) + fair value gaps: sweep, retest or rejection"

    @dataclass
    class Params:
        mode: str = "level_fvg"  # "level_fvg" | "sweep_fvg" | "fvg_retest" | "level_reject"
        session_start: int = 720
        session_end: int = 1100
        rr: float = 2.0
        stop_buffer_atr: float = 0.1
        max_risk_atr: float = 3.0
        min_risk_atr: float = 0.3
        sweep_lookback: int = 6  # bars between the sweep and the gap (sweep_fvg)
        fvg_lookback: int = 20  # how old a gap may be (fvg_retest)
        min_gap_atr: float = 0.15  # ignore tiny gaps
        level_near_atr: float = 0.5  # level_fvg: gap must sit within this many ATR of a key level
        trend_filter: bool = True  # level_fvg: only trade with the EMA 20/50 trend
        use_pd: bool = True  # prior-day high/low
        use_on: bool = True  # overnight high/low
        do_long: bool = True
        do_short: bool = True
        news_mode: str = "skip_7_8"
        skip_bars: str = ""

    # ───────────────────────── shared state ─────────────────────────

    def _levels(self, bars: list[Bar]):
        """Per bar: (supports, resistances) known at that bar, from completed sessions only."""
        p = self.p
        out = []
        day_hi = day_lo = None
        prev_hi = prev_lo = None
        on_hi = on_lo = None
        on_done_hi = on_done_lo = None
        cur_day = None
        for b in bars:
            td = _trading_day(b.time)
            t = hhmm(b.time)
            if td != cur_day:  # new CME day at 17:00
                if cur_day is not None:
                    prev_hi, prev_lo = day_hi, day_lo
                cur_day, day_hi, day_lo = td, b.high, b.low
                on_hi, on_lo = b.high, b.low
                on_done_hi = on_done_lo = None
            else:
                day_hi, day_lo = max(day_hi, b.high), min(day_lo, b.low)
            in_overnight = t >= 1700 or t < p.session_start
            sup, res = [], []
            if p.use_pd and prev_hi is not None:
                sup.append(("PDL", prev_lo))
                res.append(("PDH", prev_hi))
            if p.use_on and on_done_hi is not None:
                sup.append(("ONL", on_done_lo))
                res.append(("ONH", on_done_hi))
            out.append((sup, res))
            # update the overnight range after recording (so the current bar never sees itself)
            if in_overnight:
                on_hi, on_lo = max(on_hi, b.high), min(on_lo, b.low)
            elif on_done_hi is None:
                on_done_hi, on_done_lo = on_hi, on_lo
        return out

    def _in_session(self, t: int) -> bool:
        a, b = self.p.session_start, self.p.session_end
        return (a <= t < b) if a <= b else (t >= a or t < b)

    # ───────────────────────── signals ─────────────────────────

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
        ema20, ema50, ema200 = ta.ema(c, 20), ta.ema(c, 50), ta.ema(c, 200)
        levels = self._levels(bars)
        skip = {int(x) for x in str(p.skip_bars).replace(" ", "").split(",") if x}

        from ..bars import day as _day

        last_sweep_low: tuple[int, float] | None = None  # (bar index, sweep extreme) for longs
        last_sweep_high: tuple[int, float] | None = None
        bull_gaps: list[tuple[int, float, float]] = []  # (formed at i, bottom, top), untouched
        bear_gaps: list[tuple[int, float, float]] = []
        lvl_bull: list[tuple[int, float, float, str, float]] = []  # + (level name, level price)
        lvl_bear: list[tuple[int, float, float, str, float]] = []

        for i in range(2, n):
            a = atr[i]
            if a is None or a <= 0:
                continue
            t = hhmm(bars[i].time)
            sup, res = levels[i]

            # track sweeps: wick through a level, close back on the other side
            for _, lv in sup:
                if lo[i] < lv and c[i] > lv:
                    last_sweep_low = (i, lo[i] if last_sweep_low is None or i - last_sweep_low[0] > p.sweep_lookback else min(lo[i], last_sweep_low[1]))
            for _, lv in res:
                if h[i] > lv and c[i] < lv:
                    last_sweep_high = (i, h[i] if last_sweep_high is None or i - last_sweep_high[0] > p.sweep_lookback else max(h[i], last_sweep_high[1]))

            # gaps formed on this bar
            new_bull = lo[i] > h[i - 2] and (lo[i] - h[i - 2]) >= p.min_gap_atr * a
            new_bear = h[i] < lo[i - 2] and (lo[i - 2] - h[i]) >= p.min_gap_atr * a

            news = ctx.news_days.get(_day(bars[i].time))
            blocked = bool(news) and (p.news_mode == "skip_session" or (p.news_mode == "skip_7_8" and 700 <= t < 800))
            tradable = self._in_session(t) and not blocked and t not in skip

            sig: Signal | None = None
            if tradable:
                if p.mode == "sweep_fvg":
                    if p.do_long and new_bull and last_sweep_low and i - last_sweep_low[0] <= p.sweep_lookback:
                        stop = last_sweep_low[1] - p.stop_buffer_atr * a
                        sig = self._mk(1, c[i], stop, a, "sweep + bullish FVG")
                    elif p.do_short and new_bear and last_sweep_high and i - last_sweep_high[0] <= p.sweep_lookback:
                        stop = last_sweep_high[1] + p.stop_buffer_atr * a
                        sig = self._mk(-1, c[i], stop, a, "sweep + bearish FVG")
                elif p.mode == "fvg_retest":
                    up = None not in (ema20[i], ema50[i], ema200[i]) and ema20[i] > ema50[i] and c[i] > ema200[i]
                    dn = None not in (ema20[i], ema50[i], ema200[i]) and ema20[i] < ema50[i] and c[i] < ema200[i]
                    if p.do_long and up:
                        for k, bot, top in reversed(bull_gaps):
                            if i - k <= p.fvg_lookback and k < i - 1 and lo[i] <= top and lo[i] > bot and c[i] > top and c[i] > o[i]:
                                sig = self._mk(1, c[i], bot - p.stop_buffer_atr * a, a, "bullish FVG retest")
                                break
                    if sig is None and p.do_short and dn:
                        for k, bot, top in reversed(bear_gaps):
                            if i - k <= p.fvg_lookback and k < i - 1 and h[i] >= bot and h[i] < top and c[i] < bot and c[i] < o[i]:
                                sig = self._mk(-1, c[i], top + p.stop_buffer_atr * a, a, "bearish FVG retest")
                                break
                elif p.mode == "level_fvg":
                    up = ema20[i] is not None and ema50[i] is not None and ema20[i] > ema50[i]
                    dn = ema20[i] is not None and ema50[i] is not None and ema20[i] < ema50[i]
                    if p.do_long and (up or not p.trend_filter):
                        for k, bot, top, name, lv in reversed(lvl_bull):
                            if k < i - 1 and lo[i] <= top and lo[i] > min(bot, lv) - 0.25 * a and c[i] > top and c[i] > o[i]:
                                sig = self._mk(1, c[i], min(bot, lv) - p.stop_buffer_atr * a, a, f"bullish FVG at {name}")
                                break
                    if sig is None and p.do_short and (dn or not p.trend_filter):
                        for k, bot, top, name, lv in reversed(lvl_bear):
                            if k < i - 1 and h[i] >= bot and h[i] < max(top, lv) + 0.25 * a and c[i] < bot and c[i] < o[i]:
                                sig = self._mk(-1, c[i], max(top, lv) + p.stop_buffer_atr * a, a, f"bearish FVG at {name}")
                                break
                elif p.mode == "level_reject":
                    rng = h[i] - lo[i]
                    if rng > 0:
                        if p.do_long:
                            for name, lv in sup:
                                if lo[i] < lv < c[i] and (c[i] - lo[i]) / rng >= 0.67:
                                    sig = self._mk(1, c[i], lo[i] - p.stop_buffer_atr * a, a, f"{name} rejection")
                                    break
                        if sig is None and p.do_short:
                            for name, lv in res:
                                if h[i] > lv > c[i] and (h[i] - c[i]) / rng >= 0.67:
                                    sig = self._mk(-1, c[i], h[i] + p.stop_buffer_atr * a, a, f"{name} rejection")
                                    break
            out[i] = sig

            # gap bookkeeping (after using existing gaps on this bar)
            bull_gaps = [(k, b_, t_) for k, b_, t_ in bull_gaps if i - k <= p.fvg_lookback and lo[i] > b_]  # filled -> gone
            bear_gaps = [(k, b_, t_) for k, b_, t_ in bear_gaps if i - k <= p.fvg_lookback and h[i] < t_]
            if out[i] is not None:  # a gap is used once
                lvl_bull = [g for g in lvl_bull if not (out[i].direction > 0 and lo[i] <= g[2])]
                lvl_bear = [g for g in lvl_bear if not (out[i].direction < 0 and h[i] >= g[1])]
            lvl_bull = [g for g in lvl_bull if i - g[0] <= p.fvg_lookback and lo[i] > min(g[1], g[4]) - 0.25 * a]
            lvl_bear = [g for g in lvl_bear if i - g[0] <= p.fvg_lookback and h[i] < max(g[2], g[4]) + 0.25 * a]
            if new_bull:
                bull_gaps.append((i, h[i - 2], lo[i]))
                # gap launched from support: the 3-bar move's low is near a support level
                base = min(lo[i - 2], lo[i - 1])
                for name, lv in sup:
                    if abs(base - lv) <= p.level_near_atr * a or (base < lv < lo[i]):
                        lvl_bull.append((i, h[i - 2], lo[i], name, lv))
                        break
            if new_bear:
                bear_gaps.append((i, h[i], lo[i - 2]))
                base = max(h[i - 2], h[i - 1])
                for name, lv in res:
                    if abs(base - lv) <= p.level_near_atr * a or (h[i] < lv < base):
                        lvl_bear.append((i, h[i], lo[i - 2], name, lv))
                        break
        return out

    def _mk(self, d: int, entry: float, stop: float, a: float, why: str) -> Signal | None:
        risk = (entry - stop) * d
        if risk <= self.p.min_risk_atr * a or risk > self.p.max_risk_atr * a:
            return None
        return Signal(d, stop=stop, target=entry + d * self.p.rr * risk, info={"setup": why})

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        if not bars:
            return []
        sup, res = self._levels(bars)[-1]
        rows: list[ChecklistRow] = [(f"{name}", f"{lv:,.1f}", None) for name, lv in res + sup]
        rows.insert(0, ("Mode", self.p.mode.replace("_", " "), None))
        return rows
