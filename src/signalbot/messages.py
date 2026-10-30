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


def sizing(w: Watch, symbol: str, runner: float = 0.0) -> tuple[str, float, int, int]:
    """(contract you trade, $ per point, how many, how many ride as the runner)."""
    first = next(iter(contract_names(w, symbol).items()), (symbol, 1.0))
    keep = round(w.size * runner) if runner else 0
    return first[0], first[1], w.size, keep


def _money_lines(w: Watch, risk: float, reward: float, symbol: str = "") -> list[str]:
    return [f"{name}: risk {_usd(risk * ppt)} · reward {_usd(reward * ppt)}" for name, ppt in contract_names(w, symbol).items()]


def entry(t: Trade, w: Watch, symbol: str, res: Result, bar_ms: int) -> tuple[str, str]:
    side = "BUY" if t.direction > 0 else "SELL"
    below, above = ("below", "above") if t.direction > 0 else ("above", "below")
    closed_at = datetime.fromtimestamp((t.signal_time + bar_ms) / 1000, tz=CHICAGO).strftime("%I:%M %p CT")
    extra = " · ".join(f"{v}" for v in t.info.values())
    levels = f"{side} {_px(t.signal_close)} | SL {_px(t.stop)} | TP {_px(t.target)}"
    unit, ppt, size, keep = sizing(w, symbol, t.runner)
    if size > 1:
        levels = f"{side} {size}x {_px(t.signal_close)} | SL {_px(t.stop)} | TP {_px(t.target)}"
    lines = [
        levels,
        *([f"Setup: {extra}"] if extra else []),
        f"Signal on the bar that closed {closed_at}, close {_px(t.signal_close)}",
        f"FROM YOUR FILL: stop {t.risk:.2f} pts {below}, target {t.reward:.2f} pts {above}",
        *_money_lines(w, t.risk, t.reward, symbol),
        *([f"YOUR SIZE {size} {unit}: risk {_usd(t.risk * ppt * size)} total, target pays "
           f"{_usd(t.reward * ppt * (size - keep))} on {size - keep}" + (f" (+ the {keep} that ride)" if keep else "")]
          if size > 1 else []),
    ]
    wins, n = res.win_rate(t.direction)
    if n >= 5:
        lines.append(f"{'Longs' if t.direction > 0 else 'Shorts'} in the bot's history window: {wins}/{n} won")
    names = " or ".join(contract_names(w, symbol)) or symbol
    if t.runner and keep:
        lines.append(f"RUNNER TRADE: {size - keep} exit at TP, {keep} ride. When TP fills, move the stop on the {keep} left to your entry; "
                     f"the bot alerts EXIT RUNNER when the trend breaks (change of character).")
        lines.append(f"On Robinhood: {'buy' if t.direction > 0 else 'sell'} {size} {unit}, stop {t.risk:.2f} pts {below} your fill for all {size}, "
                     f"limit {t.reward:.2f} pts {above} for {size - keep} of them. Signal only, not advice.")
    elif size > 1:
        lines.append(f"On Robinhood: {'buy' if t.direction > 0 else 'sell'} {size} {unit}, then right away place a stop "
                     f"{t.risk:.2f} pts {below} and a limit {t.reward:.2f} pts {above} your fill price. Signal only, not advice.")
    else:
        lines.append(f"On Robinhood: {'buy' if t.direction > 0 else 'sell'} {names}, then right away place a stop "
                     f"{t.risk:.2f} pts {below} and a limit {t.reward:.2f} pts {above} your fill price. Signal only, not advice.")
    tag = "PAPER " if w.paper else ""
    # the title shows in bold on the phone / Mac banner: lead with the numbers you need to place the order
    return f"{tag}{levels} | {symbol} {w.name}", "\n".join(lines)


def missed_entry(t: Trade, w: Watch, symbol: str, bar_ms: int) -> tuple[str, str]:
    """A signal that fired while the bot was offline (Mac asleep, no data) and is still open."""
    side = "LONG" if t.direction > 0 else "SHORT"
    when = datetime.fromtimestamp((t.signal_time + bar_ms) / 1000, tz=CHICAGO).strftime("%a %I:%M %p CT")
    entry_px = t.entry_price or t.signal_close
    lines = [
        f"The bot was offline when this signal fired ({when}). The trade is still open in the backtest:",
        f"{side} from {_px(entry_px)} | SL {_px(t.active_stop)} | TP {_px(t.target)}",
        "Don't enter now unless price is still near the entry. If you're already in it, keep your stop and target;",
        "the target / stop / exit alerts for it will come as usual.",
    ]
    return f"{'PAPER ' if w.paper else ''}MISSED {side} (still open) | {symbol} {w.name}", "\n".join(lines)


def partial(t: Trade, w: Watch, symbol: str) -> tuple[str, str]:
    """Runner trades: the target took the first part off; the rest keeps going."""
    entry_px = t.entry_price or t.signal_close
    first = ((t.part_price or 0) - entry_px) * t.direction
    unit, ppt, size, keep = sizing(w, symbol, t.runner)
    done = size - keep
    body = (f"Target {_px(t.part_price or 0)} hit: {done} {unit} closed +{first:.2f} pts = +{_usd(first * ppt * done)}.\n"
            + f"NOW: move the stop on the {keep} left to your entry ({_px(entry_px)}).\n"
            + "Let it ride. The bot sends EXIT RUNNER when the trend breaks (change of character).")
    return f"{'PAPER ' if w.paper else ''}{symbol} {w.name}: TARGET HIT, move stop to entry {_px(entry_px)}", body


def exit_(t: Trade, w: Watch, symbol: str) -> tuple[str, str]:
    if t.part_price is not None:  # runner trade: the first part is already closed
        return _runner_exit(t, w, symbol)
    side = "LONG" if t.direction > 0 else "SHORT"
    pts = t.points or 0.0
    sign = "+" if pts >= 0 else "-"
    reason = {"target": "TARGET", "stop": "STOP", "time": "TIME EXIT"}.get(t.exit_reason or "", "EXIT")
    money = " · ".join(f"{name} {sign}{_usd(abs(pts) * ppt)}" for name, ppt in contract_names(w, symbol).items())
    unit, ppt0, size, _ = sizing(w, symbol)
    total = f"YOUR {size} {unit}: {sign}{_usd(abs(pts) * ppt0 * size)} total\n" if size > 1 else ""
    body = (
        f"{side} from {_px(t.entry_price or t.signal_close)} closed at {_px(t.exit_price or 0)} "
        f"({sign}{abs(pts):.2f} pts)\n" + total
        + (f"{money} per contract (before costs)\n" if money else "")
        + ("Close your position now (end-of-day exit)." if t.exit_reason == "time"
           else "If your stop / limit orders were in place, the position should be closed. Check Robinhood and cancel the other order.")
    )
    return f"{'PAPER ' if w.paper else ''}{symbol} {w.name}: {reason}", body


def _runner_exit(t: Trade, w: Watch, symbol: str) -> tuple[str, str]:
    entry_px = t.entry_price or t.signal_close
    rest = ((t.exit_price or 0) - entry_px) * t.direction
    first = ((t.part_price or 0) - entry_px) * t.direction
    pts = t.points or 0.0
    sign = "+" if pts >= 0 else "-"
    unit, ppt, size, keep = sizing(w, symbol, t.runner)
    money = f"{size} {unit}: {sign}{_usd(abs(pts) * ppt * size)} total"
    if t.exit_reason == "choch":
        title = f"EXIT RUNNER NOW ~{_px(t.exit_price or 0)}"
        act = f"Close the {keep} remaining {unit} at market now: the trend broke (change of character). No order is waiting for this exit."
    elif t.exit_reason == "time":
        title = "EXIT RUNNER (end of day)"
        act = "Close the remaining contracts now."
    else:
        title = "RUNNER STOPPED at entry"
        act = "Your stop at entry should have closed the rest. Check Robinhood."
    body = (f"Runner {'+' if rest >= 0 else '-'}{abs(rest):.2f} pts (exit {_px(t.exit_price or 0)}); first part was {first:+.2f} pts.\n"
            + f"Whole trade {sign}{abs(pts):.2f} pts per contract on average ({money}), before costs.\n"
            + act)
    return f"{'PAPER ' if w.paper else ''}{symbol} {w.name}: {title}", body


def news(name: str) -> tuple[str, str]:
    return f"NEWS DAY: {name} at 7:30 CT", "Gold: no entries 7:00–8:00 CT. Signals resume after 8:00 CT."


def news_info(items: list[tuple[str, str]]) -> tuple[str, str]:
    """Heads-up for other high-impact US releases (PPI, retail sales, Fed speakers...). Not blocked."""
    lines = [f"{t} CT  {name}" for t, name in items]
    return ("HIGH-IMPACT NEWS TODAY (not blocked)",
            "\n".join(lines) + "\nGold can spike at these times. Signals still fire; consider skipping one that lands right on a release.")


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
