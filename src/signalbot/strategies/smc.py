"""Smart Money Concepts (SMC) - market structure + order block / FVG pullback entries, 15-minute bars.

Structure (no look-ahead): a swing high/low is confirmed ``swing_len`` bars after it prints.
  * BOS   (break of structure)  - a close beyond the last swing in the direction of the trend
  * CHoCH (change of character) - the first close beyond the last swing AGAINST the trend
After a break, the setup is armed:
  * zone "ob"  - the order block: the last opposite-colour candle before the move that broke
                 structure (bullish break -> last down candle; its low..high is the zone)
  * zone "fvg" - the fair value gap left by the breaking move (the most recent one in the leg)
  * premium/discount - longs only if the zone sits in the lower half (discount) of the leg
                 (leg low -> break high), shorts only in the upper half (premium)
Entry: a later bar trades into the zone and closes back out of it in the trade direction
(confirmation candle), within ``expiry`` bars. The zone is dead once price closes through it.
Stop beyond the zone (or the leg extreme with ``stop_at_leg``); target = rr x risk.
``breaks``: "choch" = reversals only, "bos" = trend continuation only, "both".
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import indicators as ta
from ..bars import Bar, day, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy


@register
class Smc(Strategy):
    name = "smc"
    description = "Smart Money Concepts: BOS / CHoCH structure breaks, then order-block or FVG pullback entries"

    @dataclass
    class Params:
        breaks: str = "both"  # "choch" | "bos" | "both"
        zone: str = "ob"  # "ob" | "fvg"
        swing_len: int = 3  # bars each side to confirm a swing point
        require_fvg: bool = True  # the breaking move must leave a fair value gap (displacement)
        discount: bool = True  # longs from the lower half of the leg, shorts from the upper half
        expiry: int = 24  # bars the setup stays armed (24 x 15m = 6 h)
        stop_at_leg: bool = False  # stop beyond the leg extreme instead of the zone
        stop_buffer_atr: float = 0.1
        min_risk_atr: float = 0.3
        max_risk_atr: float = 3.0
        rr: float = 2.0
        session_start: int = 720
        session_end: int = 1100
        do_long: bool = True
        do_short: bool = True
        news_mode: str = "skip_7_8"
        skip_bars: str = ""

    def _in_session(self, t: int) -> bool:
        a, b = self.p.session_start, self.p.session_end
        return (a <= t < b) if a <= b else (t >= a or t < b)

    def _structure(self, bars: list[Bar]):
        """Yields per bar the state the strategy needs. Kept separate so checklist() can reuse it."""
        p = self.p
        n, L = len(bars), p.swing_len
        h = [b.high for b in bars]
        lo = [b.low for b in bars]
        c = [b.close for b in bars]
        o = [b.open for b in bars]
        swing_hi = swing_lo = None  # (index, price) of the latest confirmed, unbroken swing
        trend = 0
        events: list[tuple | None] = [None] * n  # (direction, kind, zone_bot, zone_top, leg_lo, leg_hi)
        trends = [0] * n
        for i in range(n):
            j = i - L  # candidate pivot confirmed now
            if j >= L:
                if h[j] == max(h[j - L : i + 1]) and h[j] > max(h[j - L : j] + h[j + 1 : i + 1], default=-1e18) - 1e-12:
                    swing_hi = (j, h[j])
                if lo[j] == min(lo[j - L : i + 1]) and lo[j] < min(lo[j - L : j] + lo[j + 1 : i + 1], default=1e18) + 1e-12:
                    swing_lo = (j, lo[j])
            ev = None
            if swing_hi and c[i] > swing_hi[1]:
                kind = "BOS" if trend == 1 else "CHoCH"
                start = swing_lo[0] if swing_lo and swing_lo[0] < i else max(0, i - 20)
                leg_lo = min(lo[start : i + 1])
                origin = min(range(start, i + 1), key=lambda k: lo[k])
                ev = (1, kind, *self._zone(1, origin, i, o, h, lo, c), leg_lo, h[i])
                trend, swing_hi = 1, None
            elif swing_lo and c[i] < swing_lo[1]:
                kind = "BOS" if trend == -1 else "CHoCH"
                start = swing_hi[0] if swing_hi and swing_hi[0] < i else max(0, i - 20)
                leg_hi = max(h[start : i + 1])
                origin = max(range(start, i + 1), key=lambda k: h[k])
                ev = (-1, kind, *self._zone(-1, origin, i, o, h, lo, c), lo[i], leg_hi)
                trend, swing_lo = -1, None
            events[i] = ev
            trends[i] = trend
        return events, trends

    def _zone(self, d, origin, i, o, h, lo, c):
        """(ob_bot, ob_top, fvg_bot, fvg_top) for the leg origin..i; None where absent."""
        ob = None
        for k in range(i, max(origin, 0) - 1, -1):  # last opposite candle at or after the leg origin
            if (d > 0 and c[k] < o[k]) or (d < 0 and c[k] > o[k]):
                ob = (lo[k], h[k])
                break
        if ob is None and 0 <= origin:  # origin bar itself
            ob = (lo[origin], h[origin])
        fvg = None
        for k in range(i, max(origin + 2, 2) - 1, -1):
            if d > 0 and lo[k] > h[k - 2]:
                fvg = (h[k - 2], lo[k])
                break
            if d < 0 and h[k] < lo[k - 2]:
                fvg = (h[k], lo[k - 2])
                break
        return (*ob, *(fvg or (None, None)))

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
        events, _ = self._structure(bars)
        skip = {int(x) for x in str(p.skip_bars).replace(" ", "").split(",") if x}
        armed = None  # (dir, born, bot, top, leg_lo, leg_hi, kind)

        for i in range(n):
            a = atr[i]
            ev = events[i]
            if ev is not None:  # a fresh break replaces whatever was armed
                d, kind, ob_b, ob_t, fv_b, fv_t, leg_lo, leg_hi = ev
                bot, top = (ob_b, ob_t) if p.zone == "ob" else (fv_b, fv_t)
                ok = bot is not None and (p.breaks == "both" or p.breaks.lower() == kind.lower())
                ok = ok and (not p.require_fvg or fv_b is not None)
                ok = ok and ((d > 0 and p.do_long) or (d < 0 and p.do_short))
                if ok and p.discount:
                    mid = (leg_lo + leg_hi) / 2
                    ok = top <= mid if d > 0 else bot >= mid
                armed = (d, i, bot, top, leg_lo, leg_hi, kind) if ok else None
                continue
            if armed is None or a is None or a <= 0:
                continue
            d, born, bot, top, leg_lo, leg_hi, kind = armed
            if i - born > p.expiry or (d > 0 and c[i] < bot) or (d < 0 and c[i] > top):
                armed = None
                continue
            t = hhmm(bars[i].time)
            news = ctx.news_days.get(day(bars[i].time))
            blocked = bool(news) and (p.news_mode == "skip_session" or (p.news_mode == "skip_7_8" and 700 <= t < 800))
            if not self._in_session(t) or blocked or t in skip:
                continue
            if d > 0 and lo[i] <= top and c[i] > top and c[i] > o[i]:
                stop = (leg_lo if p.stop_at_leg else bot) - p.stop_buffer_atr * a
                out[i] = self._mk(1, c[i], stop, a, f"{kind} up, {p.zone.upper()} retest")
            elif d < 0 and h[i] >= bot and c[i] < bot and c[i] < o[i]:
                stop = (leg_hi if p.stop_at_leg else top) + p.stop_buffer_atr * a
                out[i] = self._mk(-1, c[i], stop, a, f"{kind} down, {p.zone.upper()} retest")
            if out[i] is not None:
                armed = None  # one entry per zone
        return out

    def _mk(self, d: int, entry: float, stop: float, a: float, why: str) -> Signal | None:
        risk = (entry - stop) * d
        if risk <= self.p.min_risk_atr * a or risk > self.p.max_risk_atr * a:
            return None
        return Signal(d, stop=stop, target=entry + d * self.p.rr * risk, info={"setup": why})

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        if not bars:
            return []
        events, trends = self._structure(bars)
        last = next(((i, e) for i in range(len(events) - 1, -1, -1) if (e := events[i])), None)
        rows: list[ChecklistRow] = [("Structure", {1: "bullish", -1: "bearish"}.get(trends[-1], "unclear"), None)]
        if last:
            i, (d, kind, ob_b, ob_t, fv_b, fv_t, *_ ) = last
            rows.append(("Last break", f"{kind} {'up' if d > 0 else 'down'}, {len(bars) - 1 - i} bars ago", None))
            rows.append(("Order block", f"{ob_b:,.1f} - {ob_t:,.1f}", None))
            if fv_b is not None:
                rows.append(("FVG", f"{fv_b:,.1f} - {fv_t:,.1f}", None))
        return rows
