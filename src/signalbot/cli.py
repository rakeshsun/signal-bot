"""Command line: signalbot run | check | backtest | strategies | test-notify."""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path

from . import messages, news, strategies
from .bars import CHICAGO, interval_ms
from .config import Settings, Watch, load
from .engine import Result
from .notify import Notifier
from .runner import evaluate, news_days, run_live
from .strategies.base import Context


def _strategy_for(w: Watch):
    cls = strategies.get(w.strategy)
    return cls(cls.make_params(w.params))


async def _history(s: Settings, w: Watch, days: int):
    from .data import tastytrade as tt

    session = tt.make_session()
    inst = await tt.resolve(session, w.symbol, w.product)
    return inst.symbol, await tt.fetch_history(session, inst, w.interval, days)


async def cmd_check(s: Settings, names: list[str]) -> None:
    nd = news_days(s, fetch=True)
    for w in [s.watch(n) for n in names] if names else [w for w in s.watches if w.enabled]:
        symbol, bars = await _history(s, w, s.history_days)
        strat = _strategy_for(w)
        ctx = Context(symbol, nd)
        res = evaluate(strat, bars, ctx)
        print(messages.status(w, symbol, res, strat.checklist(bars, ctx)), "\n")


async def cmd_backtest(s: Settings, name: str, days: int, compare: Path | None, csv_in: Path | None) -> None:
    w = s.watch(name)
    if csv_in:
        from .data import csvfile

        bars, symbol = csvfile.load(csv_in), csv_in.stem
    else:
        symbol, bars = await _history(s, w, days)
    res = evaluate(_strategy_for(w), bars, Context(symbol, news.from_config(s.news_days)))
    print_backtest(res, w, symbol)
    if compare:
        compare_with_tradingview(res, compare, interval_ms(w.interval))


def print_backtest(res: Result, w: Watch, symbol: str) -> None:
    pv = w.point_value
    closed = [t for t in res.trades if not t.is_open]
    print(f"\n{w.name} · {w.strategy} · {symbol}: {len(res.bars)} bars, {len(res.trades)} trades\n")
    print(f"{'#':>3}  {'signal bar (CT)':<18}{'side':<6}{'entry':>10}{'exit':>10}  {'reason':<7}{'pts':>8}{'$':>10}")
    gw = gl = 0.0
    for i, t in enumerate(res.trades, 1):
        when = datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO).strftime("%Y-%m-%d %H:%M")
        side = "LONG" if t.direction > 0 else "SHORT"
        if t.is_open:
            print(f"{i:>3}  {when:<18}{side:<6}{(t.entry_price or 0):>10.2f}{'':>10}  open")
            continue
        usd = (t.points or 0) * pv
        gw, gl = gw + max(usd, 0), gl + max(-usd, 0)
        print(f"{i:>3}  {when:<18}{side:<6}{t.entry_price:>10.2f}{t.exit_price:>10.2f}  {t.exit_reason:<7}{t.points:>8.2f}{usd:>10,.0f}")
    if closed:
        wins = sum(1 for t in closed if t.won)
        pf = gw / gl if gl else float("inf")
        print(f"\nWin rate {wins}/{len(closed)} = {100 * wins / len(closed):.1f}% · profit factor {pf:.2f} · "
              f"net ${gw - gl:,.0f} per contract (point value {pv:g}) before commission/slippage")


def compare_with_tradingview(res: Result, path: Path, bar_ms: int) -> None:
    """Match the bot's trades with TradingView's list: same direction, entry bar within one bar."""
    tv = []
    with path.open() as f:
        for row in csv.DictReader(f):
            tv.append((row["direction"], datetime.fromisoformat(row["entry_time_ct"]).replace(tzinfo=CHICAGO)))
    bot = [("long" if t.direction > 0 else "short",
            datetime.fromtimestamp((t.signal_time + bar_ms) / 1000, tz=CHICAGO)) for t in res.trades]
    start = datetime.fromtimestamp(res.bars[0].time / 1000, tz=CHICAGO) + timedelta(days=5)  # warm-up
    end = datetime.fromtimestamp(res.bars[-1].time / 1000, tz=CHICAGO)
    tv = [x for x in tv if start <= x[1] <= end]
    tol = bar_ms / 1000
    matched = [x for x in tv if any(d == x[0] and abs((b - x[1]).total_seconds()) <= tol for d, b in bot)]
    print(f"\nTradingView parity: {len(matched)}/{len(tv)} TradingView trades also taken by the bot")
    for x in tv:
        if x not in matched:
            print(f"  missing: {x[0]} entry {x[1]:%Y-%m-%d %H:%M} CT")
    extra = [b for b in bot if start <= b[1] <= end and not any(d == b[0] and abs((b[1] - t).total_seconds()) <= tol for d, t in tv)]
    for d, b in extra:
        print(f"  extra:   {d} entry {b:%Y-%m-%d %H:%M} CT")


def cmd_strategies() -> None:
    for name, cls in strategies.available().items():
        if name.endswith("_example"):
            continue
        print(f"{name}\n  {cls.description}\n  params: {', '.join(cls.Params.__dataclass_fields__)}\n")


def main() -> None:
    ap = argparse.ArgumentParser(prog="signalbot", description="Trading strategy signal bot (tastytrade data, phone alerts)")
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="watch live candles for all enabled watches and send alerts")
    r.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    c = sub.add_parser("check", help="load history and print each watch's current checklist")
    c.add_argument("watch", nargs="*", help="watch name(s); default: all enabled")
    b = sub.add_parser("backtest", help="replay one watch's strategy on past candles")
    b.add_argument("watch", help="watch name from config.toml, e.g. gold")
    b.add_argument("--days", type=int, default=120)
    b.add_argument("--csv", type=Path, help="bars from a CSV file instead of tastytrade")
    b.add_argument("--compare", type=Path, help="TradingView trade-list CSV to check parity against")
    sub.add_parser("strategies", help="list available strategies and their params")
    sub.add_parser("test-notify", help="send a test notification")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not a.verbose:  # the tastytrade SDK logs every stream message at DEBUG; keep the log small
        try:
            import tastytrade  # noqa: F401  (its import sets its logger to DEBUG, so import first)
        except ImportError:
            pass
        for name in ("tastytrade", "httpx", "httpx2", "httpcore", "websockets", "anyio"):
            logging.getLogger(name).setLevel(logging.WARNING)
    if a.cmd == "strategies":
        return cmd_strategies()
    s = load(Path(a.config))
    if a.cmd == "run":
        asyncio.run(run_live(s, a.dry_run))
    elif a.cmd == "check":
        asyncio.run(cmd_check(s, a.watch))
    elif a.cmd == "backtest":
        asyncio.run(cmd_backtest(s, a.watch, a.days, a.compare, a.csv))
    elif a.cmd == "test-notify":
        Notifier(s.notify).send("Signal bot test", "If you can read this on your phone, alerts are set up.")


if __name__ == "__main__":
    main()
