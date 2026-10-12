"""Human-readable alert texts (shared by all strategies)."""

from __future__ import annotations

import re
from datetime import datetime

from .bars import CHICAGO
from .config import Watch
from .engine import Result, Trade
from .strategies.base import ChecklistRow


def _t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=CHICAGO).strftime("%a %b %d %I:%M %p CT")


def _px(x: float) -> str:
    return f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.1f}"


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def contract_suffix(symbol: str) -> str:
    """Month+year as brokers like Robinhood show it: '/GCZ6' -> 'Z26' ('' for stocks)."""
    m = re.fullmatch(r"/[A-Z0-9]+?([FGHJKMNQUVXZ])(\d{1,2})", symbol)
    if not m:
        return ""
    yy = m.group(2)
    return m.group(1) + (yy if len(yy) == 2 else f"2{yy}")


def contract_names(w: Watch, symbol: str) -> dict[str, float]:
    """config contracts ('/MGC') with the active month added ('/MGCZ26'), as you'd type them on Robinhood."""
    suf = contract_suffix(symbol)
    return {(name + suf if name.startswith("/") else name): ppt for name, ppt in w.contracts.items()}


def _money_lines(w: Watch, risk: float, reward: float, symbol: str = "") -> list[str]:
    return [f"{name}: risk {_usd(risk * ppt)} · reward {_usd(reward * ppt)}" for name, ppt in contract_names(w, symbol).items()]


def entry(t: Trade, w: Watch, symbol: str, res: Result, bar_ms: int) -> tuple[str, str]:
    side = "BUY" if t.direction > 0 else "SELL"
    below, above = ("below", "above") if t.direction > 0 else ("above", "below")
    closed_at = datetime.fromtimestamp((t.signal_time + bar_ms) / 1000, tz=CHICAGO).strftime("%I:%M %p CT")
    lines = [
        f"Signal on the bar that closed {closed_at}, close {_px(t.signal_close)}",
        f"FROM YOUR FILL: stop {t.risk:.2f} pts {below}, target {t.reward:.2f} pts {above}",
        f"(levels from signal close: SL {_px(t.stop)} · TP {_px(t.target)})",
        *_money_lines(w, t.risk, t.reward, symbol),
    ]
    wins, n = res.win_rate(t.direction)
    if n >= 5:
        lines.append(f"{'Longs' if t.direction > 0 else 'Shorts'} in the bot's history window: {wins}/{n} won")
    names = " or ".join(contract_names(w, symbol)) or symbol
    lines.append(f"On Robinhood: {'buy' if t.direction > 0 else 'sell'} {names}, then right away place a stop "
                 f"{t.risk:.2f} pts {below} and a limit {t.reward:.2f} pts {above} your fill price. Signal only, not advice.")
    extra = " · ".join(f"{v}" for v in t.info.values())
    return f"{side} {symbol} — {w.name}{(' ' + extra) if extra else ''}", "\n".join(lines)


def exit_(t: Trade, w: Watch, symbol: str) -> tuple[str, str]:
    side = "LONG" if t.direction > 0 else "SHORT"
    pts = t.points or 0.0
    sign = "+" if pts >= 0 else "-"
    reason = {"target": "TARGET", "stop": "STOP", "time": "TIME EXIT"}.get(t.exit_reason or "", "EXIT")
    money = " · ".join(f"{name} {sign}{_usd(abs(pts) * ppt)}" for name, ppt in contract_names(w, symbol).items())
    body = (
        f"{side} from {_px(t.entry_price or t.signal_close)} closed at {_px(t.exit_price or 0)} "
        f"({sign}{abs(pts):.2f} pts)\n"
        + (f"{money} per contract (before costs)\n" if money else "")
        + ("Close your position now (end-of-day exit)." if t.exit_reason == "time"
           else "If your stop / limit orders were in place, the position should be closed. Check Robinhood and cancel the other order.")
    )
    return f"{symbol} {w.name}: {reason}", body


def news(name: str) -> tuple[str, str]:
    return f"NEWS DAY: {name} at 7:30 CT", "Gold: no entries 7:00–8:00 CT. Signals resume after 8:00 CT."


def _cell(v) -> str:
    if v is True:
        return "✔"
    if v is False:
        return "✘"
    return "" if v is None else str(v)


def status(w: Watch, symbol: str, res: Result, rows: list[ChecklistRow]) -> str:
    if not res.bars:
        return f"{w.name} ({symbol}): no bars yet."
    last = res.bars[-1]
    out = [f"{w.name} · {symbol} · {w.strategy}", f"last closed bar {_t(last.time)} · close {_px(last.close)} · {len(res.bars)} bars"]
    two_sided = any(r[2] is not None for r in rows)
    for label, lo, sh in rows:
        out.append(f"  {label:<24} {'long ' if two_sided else ''}{_cell(lo)}" + (f"   short {_cell(sh)}" if two_sided else ""))
    t = res.open_trade
    if t:
        out.append(f"Open trade: {'LONG' if t.direction > 0 else 'SHORT'} since {_t(t.signal_time)} · SL {_px(t.stop)} · TP {_px(t.target)}")
    else:
        out.append("No open trade.")
    return "\n".join(out)
