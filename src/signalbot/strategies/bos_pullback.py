"""Valid BOS Pullback - break of structure, retest of the broken level, then a break of the new extreme.

Structure logic ported from LuxAlgo's "Valid BOS Pullback" TradingView indicator
(Mozilla Public License 2.0, https://mozilla.org/MPL/2.0/), so this file is MPL-2.0 too.
Trade rules (entry / stop / target / session) are ours - the indicator itself only marks the BOS.

Bullish (bearish is the mirror image):
  1. The reference is the HIGHEST confirmed swing high inside ``lookback`` bars (not yet used).
  2. Initial break: a bar closes above it by ``min_break_ticks`` (previous close was not above).
  3. The new high keeps extending; it counts once it has held for ``swing`` bars.
  4. After ``min_clean`` bars that do NOT touch the old level, a bar whose range crosses the old
     level is the valid pullback (retest).
  5. BOS: a later bar closes above the new high -> BUY signal on that close.
  Invalid: a close more than ``invalid_ticks`` back through the old level, or older than ``max_wait``.
  A fresh break the other way cancels the pending setups of the old direction.
Trade: stop beyond the pullback extreme (lowest low since the retest) by ``stop_pad`` points plus
``stop_atr`` x ATR(14), target = rr x risk.

1-year GC tests (costs included):
  3m, Asia 18:00-02:00, stop_atr 0,   rr 2.0:   154 trades, 48% wins, PF 1.87 (halves 1.96 / 1.70)
  3m, Asia 18:00-02:00, stop_atr 1.5, rr 1.25:  154 trades, 61% wins, PF 1.96 (halves 2.20 / 1.61)
  15m, all hours, swing 3, stop_atr 1.5, rr 0.75: 42 trades, 71% wins, PF 2.51 (halves 2.57 / 2.36)
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import indicators as ta
from ..bars import Bar, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy, clock, in_window, now_hhmm


@dataclass
class _Setup:
    d: int  # 1 bull, -1 bear
    stage: int  # 1 waiting for retest, 2 retested - waiting for the break of the new extreme
    start: int
    ref: float  # the old swing level that must be retested
    ext: float  # the new extreme that must be broken
    ext_bar: int | None = None
    held: bool = False
    clean: int = 0
    pull: float = float("nan")  # pullback extreme since the retest (stop goes beyond it)


def _pivots(h: list[float], lo: list[float], left: int, right: int):
    """Pivot value known at bar i (confirmed ``right`` bars after it printed), like ta.pivothigh."""
    n = len(h)
    ph: list[float | None] = [None] * n
    pl: list[float | None] = [None] * n
    for i in range(left + right, n):
        j = i - right
        c = h[j]
        if all(h[k] < c for k in range(j - left, j)) and all(h[k] <= c for k in range(j + 1, i + 1)):
            ph[i] = c
        c = lo[j]
        if all(lo[k] > c for k in range(j - left, j)) and all(lo[k] >= c for k in range(j + 1, i + 1)):
            pl[i] = c
    return ph, pl


@register
class BosPullback(Strategy):
    name = "bos_pullback"
    description = "Break of structure -> retest of the broken level -> break of the new extreme (LuxAlgo logic)"

    @dataclass
    class Params:
        swing: int = 2  # swing left = right bars
        lookback: int = 24  # bars to look back for the dominant swing high / low
        invalid_ticks: int = 20  # close this far back through the old level kills the setup
        min_clean: int = 3  # bars without touching the old level before a retest counts
        min_break_ticks: int = 1
        max_wait: int = 120  # bars
        tick: float = 0.1  # GC / MGC tick size
        rr: float = 2.0
        stop_pad: float = 0.5  # points beyond the pullback extreme
        stop_atr: float = 0.0  # extra stop room in ATR(14): survives the quick wick after a breakout
        session_start: int = 1800
        session_end: int = 200
        do_long: bool = True
        do_short: bool = True
        skip_bars: str = ""

    def _in_session(self, t: int) -> bool:
        a, b = self.p.session_start, self.p.session_end
        return (a <= t < b) if a <= b else (t >= a or t < b)

    def _run(self, bars: list[Bar]):
        """Returns (events per bar, active setups at the end). event = (dir, pull, ext) on a BOS bar.
        Cached on the bar list's identity (length + first/last bar), because the dashboard asks for
        the same answer several times per refresh."""
        key = (len(bars), bars[0].time, bars[-1].time, bars[-1].close) if bars else None
        cache = getattr(self, "_run_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        result = self._run_uncached(bars)
        self._run_cache = (key, result)
        return result

    def _run_uncached(self, bars: list[Bar]):
        p = self.p
        n = len(bars)
        h = [b.high for b in bars]
        lo = [b.low for b in bars]
        c = [b.close for b in bars]
        ph, pl = _pivots(h, lo, p.swing, p.swing)
        allow, minext = p.invalid_ticks * p.tick, p.min_break_ticks * p.tick
        active: list[_Setup] = []
        hv: list[tuple[float, int]] = []
        lv: list[tuple[float, int]] = []
        ret_hi: list[tuple[float, int]] = []
        ret_lo: list[tuple[float, int]] = []
        events: list[tuple[int, float, float] | None] = [None] * n
        for i in range(n):
            if ph[i] is not None:
                hv.append((ph[i], i - p.swing))
            if pl[i] is not None:
                lv.append((pl[i], i - p.swing))
            hv = [x for x in hv if i - x[1] <= p.lookback]
            lv = [x for x in lv if i - x[1] <= p.lookback]
            ret_hi = [x for x in ret_hi if i - x[1] <= p.lookback + p.swing]
            ret_lo = [x for x in ret_lo if i - x[1] <= p.lookback + p.swing]
            bull = bear = False
            for s in list(reversed(active)):
                invalid = i - s.start > p.max_wait or (c[i] < s.ref - allow if s.d == 1 else c[i] > s.ref + allow)
                done = False
                if not invalid:
                    if s.stage == 1:
                        if s.ext_bar is None:
                            s.ext_bar = s.start
                        if (s.d == 1 and h[i] > s.ext) or (s.d == -1 and lo[i] < s.ext):
                            s.ext, s.ext_bar, s.held = (h[i] if s.d == 1 else lo[i]), i, False
                        if i - s.ext_bar >= p.swing:
                            s.held = True
                        touched = lo[i] <= s.ref <= h[i]
                        if touched and s.held and s.clean >= p.min_clean:
                            s.stage, s.pull = 2, (lo[i] if s.d == 1 else h[i])
                        s.clean = 0 if touched else s.clean + 1
                    else:
                        s.pull = min(s.pull, lo[i]) if s.d == 1 else max(s.pull, h[i])
                        if (s.d == 1 and c[i] > s.ext) or (s.d == -1 and c[i] < s.ext):
                            done = True
                            bull, bear = bull or s.d == 1, bear or s.d == -1
                if invalid:
                    active.remove(s)
                elif done:
                    (ret_hi if s.d == 1 else ret_lo).append((s.ext, i))
                    events[i] = (s.d, s.pull, s.ext)
                    active.remove(s)
            rh = {x[0] for x in ret_hi}
            rl = {x[0] for x in ret_lo}
            active = [s for s in active if not ((s.d == 1 and s.ref in rh) or (s.d == -1 and s.ref in rl))]
            prev_hi = max(((v, b) for v, b in hv if v not in rh), default=None)
            prev_lo = min(((v, b) for v, b in lv if v not in rl), default=None)
            new_bull = (not bull and prev_hi is not None and i > prev_hi[1] and i > 0
                        and c[i] > prev_hi[0] + minext and c[i - 1] <= prev_hi[0])
            new_bear = (not bear and prev_lo is not None and i > prev_lo[1] and i > 0
                        and c[i] < prev_lo[0] - minext and c[i - 1] >= prev_lo[0])
            for s in active:
                if s.stage == 2 and s.d == 1 and prev_hi and abs(prev_hi[0] - s.ext) <= p.tick:
                    new_bull = False
                if s.stage == 2 and s.d == -1 and prev_lo and abs(prev_lo[0] - s.ext) <= p.tick:
                    new_bear = False
            if new_bull or new_bear:
                active = [s for s in active if not ((new_bull and s.d == -1) or (new_bear and s.d == 1))]
            if new_bull:
                active.append(_Setup(1, 1, i, prev_hi[0], h[i]))
            if new_bear:
                active.append(_Setup(-1, 1, i, prev_lo[0], lo[i]))
        return events, active

    def signals(self, bars: list[Bar], ctx: Context) -> list[Signal | None]:
        p = self.p
        out: list[Signal | None] = [None] * len(bars)
        if len(bars) < 4 * p.swing + 5:
            return out
        events, _ = self._run(bars)
        skip = {int(x) for x in str(p.skip_bars).replace(" ", "").split(",") if x}
        atr = ta.atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 14) if p.stop_atr else None
        for i, ev in enumerate(events):
            if ev is None:
                continue
            d, pull, ext = ev
            t = hhmm(bars[i].time)
            if not self._in_session(t) or t in skip or (d == 1 and not p.do_long) or (d == -1 and not p.do_short):
                continue
            close = bars[i].close
            stop = pull - d * (p.stop_pad + (p.stop_atr * (atr[i] or 0) if atr else 0))
            risk = (close - stop) * d
            if risk <= 0:
                continue
            out[i] = Signal(d, stop=stop, target=close + d * p.rr * risk,
                            info={"setup": f"BOS {'up' if d > 0 else 'down'} after retest, pullback {pull:,.1f}"})
        return out

    def levels(self, bars: list[Bar], ctx: Context):
        if len(bars) < 4 * self.p.swing + 5:
            return []
        _, active = self._run(bars)
        if not active:
            return []
        s = active[-1]
        buy = s.d == 1
        cancel = s.ref - self.p.invalid_ticks * self.p.tick if buy else s.ref + self.p.invalid_ticks * self.p.tick
        return [("Broken level", s.ref, "level"), (f"{'Buy' if buy else 'Sell'} trigger", s.ext, "trigger"),
                ("Cancel", cancel, "cancel")]

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        return [(a, b, None) for a, b, _ in self.summary(bars, ctx)]

    def summary(self, bars: list[Bar], ctx: Context, live: Bar | None = None) -> list[tuple[str, str, bool]]:
        if len(bars) < 4 * self.p.swing + 5:
            return []
        _, active = self._run(bars)
        p = self.p
        now = live.close if live is not None else bars[-1].close
        if not in_window(now_hhmm(), p.session_start, p.session_end):
            return [("What to do", f"Nothing. This strategy only trades {clock(p.session_start)}–{clock(p.session_end)} CT.", False)]
        mins = int((bars[-1].time - bars[-2].time) / 60000) if len(bars) > 1 else 3
        rows: list[tuple[str, str, bool]] = []
        if not active:
            return [("What to do", "Nothing. No setup right now.", False)]
        s = active[-1]  # the newest setup is the one that matters
        buy = s.d == 1
        side = "BUY" if buy else "SELL"
        cancel = s.ref - p.invalid_ticks * p.tick if buy else s.ref + p.invalid_ticks * p.tick
        if s.stage == 1:
            rows.append(("What to do", "Nothing yet. Just wait.", False))
            rows.append((f"{side} alert comes if",
                         f"gold {'drops' if buy else 'rises'} back to {s.ref:,.1f}, then {'climbs above' if buy else 'falls below'} "
                         f"{s.ext:,.1f} (gold is {now:,.1f} now)", False))
        else:
            rows.append(("What to do", f"Get ready. A {side} alert may come soon.", True))
            rows.append((f"{side} alert comes if", f"a {mins}-min bar closes {'above' if buy else 'below'} {s.ext:,.1f} (gold is {now:,.1f} now)", True))
        rows.append(("Setup is off if", f"gold closes {'below' if buy else 'above'} {cancel:,.1f}", False))
        return rows
