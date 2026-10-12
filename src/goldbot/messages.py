"""Human-readable alert texts."""

from __future__ import annotations

from datetime import datetime

from .strategy import CHICAGO, Checks, Result, Trade

MGC_PER_PT = 10.0  # micro gold: $10 per point
GC_PER_PT = 100.0  # full gold: $100 per point


def _t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=CHICAGO).strftime("%a %b %d %I:%M %p CT")


def _px(x: float) -> str:
    return f"{x:,.1f}"


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def entry(trade: Trade, symbol: str, res: Result) -> tuple[str, str]:
    side = "BUY" if trade.direction > 0 else "SELL"
    below, above = ("below", "above") if trade.direction > 0 else ("above", "below")
    reward = abs(trade.target - trade.signal_close)
    wins, n = res.win_rate(trade.direction)
    bar_close = datetime.fromtimestamp(trade.signal_time / 1000 + 15 * 60, tz=CHICAGO).strftime("%I:%M %p CT")
    lines = [
        f"Signal on the bar that closed {bar_close}, close {_px(trade.signal_close)}",
        f"FROM YOUR FILL: stop {trade.risk:.1f} pts {below}, target {reward:.1f} pts {above}",
        f"(levels from signal close: SL {_px(trade.stop)} · TP {_px(trade.target)})",
        f"/MGC: risk {_usd(trade.risk * MGC_PER_PT)} · reward {_usd(reward * MGC_PER_PT)}",
        f"/GC:  risk {_usd(trade.risk * GC_PER_PT)} · reward {_usd(reward * GC_PER_PT)}",
    ]
    if n >= 5:
        lines.append(f"{'Longs' if trade.direction > 0 else 'Shorts'} in the bot's history window: {wins}/{n} won")
    lines.append("Use a bracket order (stop + target attached). Educational signal, not advice.")
    return f"{side} {symbol} — gold signal 8/8", "\n".join(lines)


def exit_(trade: Trade, symbol: str) -> tuple[str, str]:
    side = "LONG" if trade.direction > 0 else "SHORT"
    pts = trade.points or 0.0
    sign = "+" if pts >= 0 else "-"
    reason = "TARGET" if trade.exit_reason == "target" else "STOP"
    body = (
        f"{side} from {_px(trade.entry_price or trade.signal_close)} hit {reason} at {_px(trade.exit_price or 0)} "
        f"({sign}{abs(pts):.1f} pts)\n"
        f"/MGC {sign}{_usd(abs(pts) * MGC_PER_PT)} · /GC {sign}{_usd(abs(pts) * GC_PER_PT)} per contract (before costs)\n"
        "If your bracket order is in place, it should have closed by itself. Check your position."
    )
    return f"{symbol} trade closed: {reason}", body


def news(name: str) -> tuple[str, str]:
    return f"NEWS DAY: {name} at 7:30 CT", "No gold entries 7:00–8:00 CT. Signals resume after 8:00 CT."


def checklist(ck: Checks | None) -> str:
    if ck is None:
        return "  (not enough history yet)"
    rows = []
    for name, lo, sh in zip(Checks.NAMES, ck.long, ck.short):
        rows.append(f"  {name:<22} long {'✔' if lo else '✘'}   short {'✔' if sh else '✘'}")
    rows.append(f"  {'Checks passed':<22} long {sum(ck.long)}/8   short {sum(ck.short)}/8")
    return "\n".join(rows)


def status(res: Result, symbol: str) -> str:
    if not res.bars:
        return "No bars yet."
    last = res.bars[-1]
    lines = [
        f"{symbol}: last closed bar {_t(last.time)} · close {_px(last.close)} · bars loaded {len(res.bars)}",
        checklist(res.checks[-1]),
    ]
    open_trades = [t for t in res.trades if t.is_open]
    if open_trades:
        t = open_trades[-1]
        lines.append(
            f"Open trade: {'LONG' if t.direction > 0 else 'SHORT'} since {_t(t.signal_time)} · "
            f"SL {_px(t.stop)} · TP {_px(t.target)}"
        )
    else:
        lines.append("No open trade.")
    return "\n".join(lines)
