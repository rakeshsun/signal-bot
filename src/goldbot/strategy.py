"""The Metals Confluence strategy, rebuilt from the TradingView Pine script.

The simulation walks closed 15-minute bars in order and reproduces the Pine
strategy's behaviour:

* a signal is evaluated on each bar's close;
* the entry fills at the next bar's open;
* stop and target are priced from the signal bar's close (stop = 1.75 x ATR,
  target = 1.5 x the stop distance) and are live from the fill bar onward;
* only one trade at a time; the trade stays open until stop or target
  (there is no end-of-day exit, the same as the Pine strategy);
* when one bar touches both stop and target, TradingView's bar-path rule
  decides which came first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import indicators as ta

CHICAGO = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class Bar:
    """One closed candle. ``time`` is the bar's OPEN time, in epoch milliseconds (UTC)."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.time / 1000, tz=CHICAGO)


@dataclass
class Params:
    """Defaults equal the TradingView strategy inputs that were backtested."""

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
    point_value: float = 100.0  # $ per point for the contract the bars come from (GC = 100)


@dataclass
class Checks:
    """The eight checklist items for one bar (long and short)."""

    long: list[bool]
    short: list[bool]

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

    @property
    def all_long(self) -> bool:
        return all(self.long)

    @property
    def all_short(self) -> bool:
        return all(self.short)


@dataclass
class Trade:
    direction: int  # +1 long, -1 short
    signal_index: int
    signal_time: int
    signal_close: float
    risk: float  # stop distance in points
    stop: float
    target: float
    entry_index: int | None = None
    entry_price: float | None = None
    exit_index: int | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "target" | "stop"

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
    """Something worth notifying about, tied to the bar on whose close it happened."""

    kind: str  # "entry" | "exit" | "news"
    bar_index: int
    bar_time: int
    trade: Trade | None = None
    text: str = ""


@dataclass
class Result:
    bars: list[Bar]
    checks: list[Checks | None]
    trades: list[Trade] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    atr: list[float | None] = field(default_factory=list)

    def win_rate(self, direction: int) -> tuple[int, int]:
        closed = [t for t in self.trades if not t.is_open and t.direction == direction]
        return sum(1 for t in closed if t.won), len(closed)


def chicago_hhmm(ts_ms: int) -> int:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=CHICAGO)
    return d.hour * 100 + d.minute


def chicago_date(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=CHICAGO).date()


def compute_checks(bars: list[Bar], p: Params) -> tuple[list[Checks | None], list[float | None]]:
    """Evaluate the eight checks on every bar. Returns (checks, atr)."""
    o = [b.open for b in bars]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    c = [b.close for b in bars]
    v = [b.volume for b in bars]
    del o

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

    out: list[Checks | None] = []
    for i in range(len(bars)):
        need = (ema_f[i], ema_s[i], ema_t[i], adx[i], di_p[i], di_m[i], rsi[i], hist[i], vol_avg[i])
        if i < 3 or any(x is None for x in need) or ema_s[i - 3] is None or rsi[i - 1] is None or hist[i - 1] is None:
            out.append(None)
            continue
        ef, es, et = ema_f[i], ema_s[i], ema_t[i]
        c1l = c[i] > es and ef > es and es > ema_s[i - 3]
        c1s = c[i] < es and ef < es and es < ema_s[i - 3]
        c2l = p.trend_len == 0 or c[i] > et
        c2s = p.trend_len == 0 or c[i] < et
        c3l = adx[i] >= p.adx_min and di_p[i] > di_m[i]
        c3s = adx[i] >= p.adx_min and di_m[i] > di_p[i]
        c4l = p.rsi_lo_long < rsi[i] < p.rsi_hi_long and rsi[i] > rsi[i - 1]
        c4s = p.rsi_lo_short < rsi[i] < p.rsi_hi_short and rsi[i] < rsi[i - 1]
        c5l = (not p.use_macd) or hist[i] > hist[i - 1]
        c5s = (not p.use_macd) or hist[i] < hist[i - 1]
        c6l = (since_l[i] if since_l[i] is not None else 9999) <= p.pb_bars
        c6s = (since_s[i] if since_s[i] is not None else 9999) <= p.pb_bars
        c7l = c[i] > h[i - 1] and c[i] > ef
        c7s = c[i] < lo[i - 1] and c[i] < ef
        c8 = (not p.use_vol) or v[i] > vol_avg[i]
        out.append(
            Checks(
                long=[c1l, c2l, c3l, c4l, c5l, c6l, c7l, c8],
                short=[c1s, c2s, c3s, c4s, c5s, c6s, c7s, c8],
            )
        )
    return out, atr


def _first_touch(bar: Bar, stop: float, target: float, direction: int) -> tuple[str, float] | None:
    """Which of stop/target the bar reached first, following TradingView's broker emulator.

    A gap through a level fills at the open. Otherwise the assumed path is
    open -> high -> low -> close when the high is nearer the open, else open -> low -> high -> close.
    """
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
    if hit_s and not hit_t:
        return "stop", stop
    if hit_t and not hit_s:
        return "target", target
    high_first = (bar.high - bar.open) <= (bar.open - bar.low)
    if direction > 0:
        return ("target", target) if high_first else ("stop", stop)
    return ("stop", stop) if high_first else ("target", target)


def run(bars: list[Bar], p: Params | None = None, news_days: dict[date, str] | None = None) -> Result:
    """Simulate the strategy over ``bars`` (all closed, oldest first)."""
    p = p or Params()
    news_days = news_days or {}
    checks, atr = compute_checks(bars, p)
    res = Result(bars=bars, checks=checks, atr=atr)

    trade: Trade | None = None  # the current trade, from signal until it is reset
    news_alerted: date | None = None

    for i, bar in enumerate(bars):
        hhmm = chicago_hhmm(bar.time)
        day = chicago_date(bar.time)

        # 1) fills and exits that happen inside this bar
        if trade is not None and trade.is_open:
            if trade.entry_index is None and i == trade.signal_index + 1:
                trade.entry_index = i
                trade.entry_price = bar.open
            if trade.entry_index is not None:
                hit = _first_touch(bar, trade.stop, trade.target, trade.direction)
                if hit is not None:
                    trade.exit_reason, trade.exit_price = hit
                    trade.exit_index = i
                    trade.exit_time = bar.time
                    res.events.append(Event("exit", i, bar.time, trade))

        # 2) Pine: "active" is cleared once flat and past the fill bar
        if trade is not None and not trade.is_open and i > trade.signal_index + 1:
            trade = None

        # 3) news heads-up on the bar that closes at 7:00 CT
        news_name = news_days.get(day)
        if news_name and p.news_mode != "off" and 645 <= hhmm < 730 and news_alerted != day:
            news_alerted = day
            res.events.append(Event("news", i, bar.time, text=news_name))

        # 4) new signal on this bar's close
        ck = checks[i]
        if trade is not None or ck is None or atr[i] is None:
            continue
        in_sess = p.session_start <= hhmm < p.session_end
        news_block = bool(news_name) and (
            p.news_mode == "skip_session" or (p.news_mode == "skip_7_8" and 700 <= hhmm < 800)
        )
        if not in_sess or news_block:
            continue
        go_long = p.do_long and ck.all_long
        go_short = p.do_short and ck.all_short and not go_long
        if not (go_long or go_short):
            continue
        d = 1 if go_long else -1
        risk = p.atr_mult * atr[i]  # type: ignore[operator]
        trade = Trade(
            direction=d,
            signal_index=i,
            signal_time=bar.time,
            signal_close=bar.close,
            risk=risk,
            stop=bar.close - d * risk,
            target=bar.close + d * p.rr * risk,
        )
        res.trades.append(trade)
        res.events.append(Event("entry", i, bar.time, trade))

    return res
