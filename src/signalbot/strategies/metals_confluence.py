"""Metals Confluence - the gold futures strategy from the TradingView script.

Enters when all 8 checks agree on a 15-minute bar that opens 7:20-9:30 CT:
EMA 20/50 stack, EMA 200 side, ADX >= 20 with DI direction, RSI zone and direction,
MACD histogram direction, pullback to EMA 20 within 5 bars, trigger bar, volume above
its 20-bar average. Stop 1.75 x ATR(14), target 1.5 x the stop. On CPI / jobs-report
days no entries 7:00-8:00 CT. Backtested on COMEX GC (Dec 2026 contract).
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import indicators as ta
from ..bars import Bar, day, hhmm
from ..engine import Signal
from . import register
from .base import ChecklistRow, Context, Strategy

NAMES = (
    "EMA 20/50 stack",
    "EMA 200 side",
    "ADX >= 20 + DI",
    "RSI zone + direction",
    "MACD histogram",
    "Pullback to fast EMA",
    "Trigger bar",
    "Volume > 20-bar avg",
)


@register
class MetalsConfluence(Strategy):
    name = "metals_confluence"
    description = "Gold 15m trend-pullback with 8 confirming checks, 7:20-9:30 CT, 1.75 ATR stop, 1.5R target"

    @dataclass
    class Params:
        fast_len: int = 20
        slow_len: int = 50
        trend_len: int = 200
        adx_len: int = 14
        adx_min: float = 20.0
        rsi_len: int = 14
        rsi_lo_long: float = 45.0
        rsi_hi_long: float = 70.0
        rsi_hi_short: float = 55.0
        rsi_lo_short: float = 30.0
        use_macd: bool = True
        use_vol: bool = True
        vol_len: int = 20
        pb_bars: int = 5
        do_long: bool = True
        do_short: bool = True
        session_start: int = 720  # HHMM Chicago, compared with the bar's open time
        session_end: int = 930
        atr_len: int = 14
        atr_mult: float = 1.75
        rr: float = 1.5
        news_mode: str = "skip_7_8"  # "off" | "skip_7_8" | "skip_session"

    def _checks(self, bars: list[Bar]) -> tuple[list[tuple[list[bool], list[bool]] | None], list[float | None]]:
        p = self.p
        h = [b.high for b in bars]
        lo = [b.low for b in bars]
        c = [b.close for b in bars]
        v = [b.volume for b in bars]
        ema_f = ta.ema(c, p.fast_len)
        ema_s = ta.ema(c, p.slow_len)
        ema_t = ta.ema(c, max(p.trend_len, 1))
        di_p, di_m, adx = ta.dmi(h, lo, c, p.adx_len, p.adx_len)
        rsi = ta.rsi(c, p.rsi_len)
        _, _, hist = ta.macd(c, 12, 26, 9)
        atr = ta.atr(h, lo, c, p.atr_len)
        vol_avg = ta.sma(v, p.vol_len)
        since_l = ta.bars_since([ef is not None and lo[i] <= ef for i, ef in enumerate(ema_f)])
        since_s = ta.bars_since([ef is not None and h[i] >= ef for i, ef in enumerate(ema_f)])

        out: list[tuple[list[bool], list[bool]] | None] = []
        for i in range(len(bars)):
            need = (ema_f[i], ema_s[i], ema_t[i], adx[i], di_p[i], di_m[i], rsi[i], hist[i], vol_avg[i])
            if i < 3 or any(x is None for x in need) or ema_s[i - 3] is None or rsi[i - 1] is None or hist[i - 1] is None:
                out.append(None)
                continue
            ef, es, et = ema_f[i], ema_s[i], ema_t[i]
            c8 = (not p.use_vol) or v[i] > vol_avg[i]
            long_ = [
                c[i] > es and ef > es and es > ema_s[i - 3],
                p.trend_len == 0 or c[i] > et,
                adx[i] >= p.adx_min and di_p[i] > di_m[i],
                p.rsi_lo_long < rsi[i] < p.rsi_hi_long and rsi[i] > rsi[i - 1],
                (not p.use_macd) or hist[i] > hist[i - 1],
                (since_l[i] if since_l[i] is not None else 9999) <= p.pb_bars,
                c[i] > h[i - 1] and c[i] > ef,
                c8,
            ]
            short = [
                c[i] < es and ef < es and es < ema_s[i - 3],
                p.trend_len == 0 or c[i] < et,
                adx[i] >= p.adx_min and di_m[i] > di_p[i],
                p.rsi_lo_short < rsi[i] < p.rsi_hi_short and rsi[i] < rsi[i - 1],
                (not p.use_macd) or hist[i] < hist[i - 1],
                (since_s[i] if since_s[i] is not None else 9999) <= p.pb_bars,
                c[i] < lo[i - 1] and c[i] < ef,
                c8,
            ]
            out.append((long_, short))
        return out, atr

    def signals(self, bars: list[Bar], ctx: Context) -> list[Signal | None]:
        p = self.p
        checks, atr = self._checks(bars)
        out: list[Signal | None] = []
        for i, bar in enumerate(bars):
            ck = checks[i]
            if ck is None or atr[i] is None:
                out.append(None)
                continue
            t = hhmm(bar.time)
            news = ctx.news_days.get(day(bar.time))
            blocked = bool(news) and (
                p.news_mode == "skip_session" or (p.news_mode == "skip_7_8" and 700 <= t < 800)
            )
            if not (p.session_start <= t < p.session_end) or blocked:
                out.append(None)
                continue
            go_long = p.do_long and all(ck[0])
            go_short = p.do_short and all(ck[1]) and not go_long
            if not (go_long or go_short):
                out.append(None)
                continue
            d = 1 if go_long else -1
            risk = p.atr_mult * atr[i]  # type: ignore[operator]
            out.append(Signal(d, stop=bar.close - d * risk, target=bar.close + d * p.rr * risk, info={"checks": "8/8"}))
        return out

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        checks, _ = self._checks(bars)
        ck = checks[-1] if checks else None
        if ck is None:
            return [("not enough history yet", None, None)]
        rows: list[ChecklistRow] = [(n, lo, sh) for n, lo, sh in zip(NAMES, ck[0], ck[1])]
        rows.append(("Checks passed", f"{sum(ck[0])}/8", f"{sum(ck[1])}/8"))
        return rows
