"""Technical indicators that reproduce TradingView Pine Script (v6) built-ins.

Every function takes plain lists and returns a list of the same length, with
``None`` where Pine would return ``na``. Matching Pine's seeding rules matters:
``ta.ema`` and ``ta.rma`` start from a simple average of the first ``length``
values, and ``ta.dmi`` / ``ta.rsi`` / ``ta.atr`` are all built on ``ta.rma``.
"""

from __future__ import annotations

from collections.abc import Sequence

Series = list[float | None]


def _smoothed(src: Sequence[float | None], length: int, alpha: float) -> Series:
    """Shared engine for ta.ema / ta.rma: seed with an SMA, then exponential smoothing.

    Pine skips leading ``na`` values: the seed is the average of the first
    ``length`` consecutive non-na values.
    """
    out: Series = [None] * len(src)
    window: list[float] = []
    prev: float | None = None
    for i, x in enumerate(src):
        if x is None:
            # Pine propagates the previous value through an na input once seeded
            out[i] = prev
            continue
        if prev is None:
            window.append(x)
            if len(window) == length:
                prev = sum(window) / length
                out[i] = prev
            continue
        prev = alpha * x + (1 - alpha) * prev
        out[i] = prev
    return out


def ema(src: Sequence[float | None], length: int) -> Series:
    """ta.ema — alpha = 2 / (length + 1)."""
    return _smoothed(src, length, 2.0 / (length + 1))


def rma(src: Sequence[float | None], length: int) -> Series:
    """ta.rma (Wilder's smoothing) — alpha = 1 / length."""
    return _smoothed(src, length, 1.0 / length)


def sma(src: Sequence[float | None], length: int) -> Series:
    """ta.sma — na until a full window of non-na values is available."""
    out: Series = [None] * len(src)
    for i in range(length - 1, len(src)):
        win = src[i - length + 1 : i + 1]
        if any(v is None for v in win):
            continue
        out[i] = sum(win) / length  # type: ignore[arg-type]
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> Series:
    """ta.tr(true): the first bar uses high - low."""
    out: Series = []
    for i in range(len(high)):
        if i == 0:
            out.append(high[i] - low[i])
        else:
            out.append(
                max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
            )
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], length: int) -> Series:
    """ta.atr = rma(true range)."""
    return rma(true_range(high, low, close), length)


def rsi(src: Sequence[float], length: int) -> Series:
    """ta.rsi — Wilder RSI on rma-smoothed gains and losses."""
    gains: Series = [None]
    losses: Series = [None]
    for i in range(1, len(src)):
        ch = src[i] - src[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    up = rma(gains, length)
    dn = rma(losses, length)
    out: Series = []
    for u, d in zip(up, dn):
        if u is None or d is None:
            out.append(None)
        elif d == 0:
            out.append(100.0)
        elif u == 0:
            out.append(0.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + u / d))
    return out


def macd(src: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    """ta.macd -> (macd line, signal line, histogram)."""
    f = ema(src, fast)
    s = ema(src, slow)
    line: Series = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist: Series = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def dmi(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], di_len: int, adx_len: int
) -> tuple[Series, Series, Series]:
    """ta.dmi -> (+DI, -DI, ADX), following the reference implementation in the Pine manual."""
    n = len(high)
    plus_dm: Series = [None] * n
    minus_dm: Series = [None] * n
    tr: Series = [None] * n  # ta.tr without handle_na: na on the first bar
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    trur = rma(tr, di_len)
    sp = rma(plus_dm, di_len)
    sm = rma(minus_dm, di_len)
    plus: Series = [None] * n
    minus: Series = [None] * n
    last_p: float | None = None
    last_m: float | None = None
    for i in range(n):
        if trur[i] is not None and sp[i] is not None and trur[i] != 0:
            last_p = 100.0 * sp[i] / trur[i]  # type: ignore[operator]
        if trur[i] is not None and sm[i] is not None and trur[i] != 0:
            last_m = 100.0 * sm[i] / trur[i]  # type: ignore[operator]
        plus[i] = last_p  # fixnan()
        minus[i] = last_m
    ratio: Series = []
    for p, m in zip(plus, minus):
        if p is None or m is None:
            ratio.append(None)
        else:
            s = p + m
            ratio.append(abs(p - m) / (1.0 if s == 0 else s))
    adx = [None if v is None else 100.0 * v for v in rma(ratio, adx_len)]
    return plus, minus, adx


def bars_since(cond: Sequence[bool]) -> list[int | None]:
    """ta.barssince — bars since the condition was last true (0 = this bar); None if never."""
    out: list[int | None] = []
    last: int | None = None
    for i, c in enumerate(cond):
        if c:
            last = i
        out.append(None if last is None else i - last)
    return out
