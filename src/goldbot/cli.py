"""Command line: goldbot run | check | backtest | test-notify."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import messages, news
from .config import Settings, load
from .notify import Notifier
from .strategy import CHICAGO, Bar, chicago_hhmm, run

log = logging.getLogger("goldbot")
BAR_MS = 15 * 60 * 1000
SETTLE_SECS = 20  # wait after a bar closes so the final candle update has arrived


# ───────────────────────── helpers ─────────────────────────


def _news_days(s: Settings, fetch: bool) -> dict[date, str]:
    days = news.from_config(s.news_days)
    if fetch and s.fetch_news_calendar:
        for d, name in news.fetch_ff().items():
            days.setdefault(d, name)
    return days


class SentLog:
    """Remembers which alerts already went out, so a restart never repeats them."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.keys: set[str] = set(json.loads(path.read_text()))
        except Exception:
            self.keys = set()

    def add(self, key: str) -> bool:
        if key in self.keys:
            return False
        self.keys.add(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self.keys)[-2000:]))
        return True


async def _session_and_future(s: Settings):
    from tastytrade import Session

    from .feed import resolve_future

    session = Session()  # reads TT_SECRET and TT_REFRESH from the environment / .env
    fut = await resolve_future(session, s.symbol, s.product)
    log.info("watching %s (streamer symbol %s, expires %s)", fut.symbol, fut.streamer_symbol, fut.expiration_date)
    return session, fut


# ───────────────────────── commands ─────────────────────────


async def cmd_check(s: Settings) -> None:
    from .feed import fetch_history

    session, fut = await _session_and_future(s)
    bars = await fetch_history(session, fut.streamer_symbol, s.history_days)
    res = run(bars, s.params, _news_days(s, fetch=True))
    print(messages.status(res, fut.symbol))


async def cmd_backtest(s: Settings, days: int, compare: Path | None, csv_in: Path | None) -> None:
    if csv_in:
        bars = load_bars_csv(csv_in)
        symbol = csv_in.stem
    else:
        from .feed import fetch_history

        session, fut = await _session_and_future(s)
        bars = await fetch_history(session, fut.streamer_symbol, days)
        symbol = fut.symbol
    res = run(bars, s.params, news.from_config(s.news_days))
    print_backtest(res, symbol, s.params.point_value)
    if compare:
        compare_with_tradingview(res, compare)


async def cmd_run(s: Settings, dry_run: bool) -> None:
    from .feed import CandleBook, stream_forever

    notifier = Notifier(s.notify, dry_run=dry_run)
    sent = SentLog(s.state_dir / "sent.json")
    session, fut = await _session_and_future(s)
    symbol = fut.symbol
    book = CandleBook()
    stream = asyncio.create_task(stream_forever(session, fut.streamer_symbol, s.history_days, book))

    # wait for the history snapshot to arrive
    t0 = time.monotonic()
    while len(book) < 300 and time.monotonic() - t0 < 120:
        await asyncio.sleep(2)
    news_days = _news_days(s, fetch=True)
    news_fetched_on = datetime.now(CHICAGO).date()
    res = run(book.closed_bars(), s.params, news_days)
    print(messages.status(res, symbol), flush=True)
    # anything that already happened before start-up counts as sent
    for ev in res.events:
        sent.add(_event_key(ev, symbol))
    notifier.send(f"Gold bot started ({symbol})", messages.status(res, symbol))

    stale_warned = False
    heartbeat_sent_on: date | None = None
    try:
        while True:
            now = time.time()
            next_close = (int(now * 1000) // BAR_MS + 1) * BAR_MS
            await asyncio.sleep(max(1.0, next_close / 1000 + SETTLE_SECS - now))
            if stream.done():
                raise RuntimeError("candle stream task stopped") from stream.exception()

            today = datetime.now(CHICAGO).date()
            if today != news_fetched_on:  # refresh the calendar once a day
                news_days = _news_days(s, fetch=True)
                news_fetched_on = today

            bars = book.closed_bars()
            if not bars:
                continue
            res = run(bars, s.params, news_days)
            for ev in res.events:
                if ev.bar_index < len(bars) - 2:  # only the newest bars can raise new alerts
                    continue
                if not sent.add(_event_key(ev, symbol)):
                    continue
                if ev.kind == "entry" and ev.trade:
                    title, body = messages.entry(ev.trade, symbol, res)
                    notifier.send(title, body, urgent=True)
                elif ev.kind == "exit" and ev.trade:
                    notifier.send(*messages.exit_(ev.trade, symbol))
                elif ev.kind == "news":
                    notifier.send(*messages.news(ev.text))

            # heartbeat so you know the bot is alive before the session
            hhmm = chicago_hhmm(int(time.time() * 1000))
            if s.heartbeat_hhmm >= 0 and hhmm >= s.heartbeat_hhmm and heartbeat_sent_on != today and today.weekday() < 5:
                heartbeat_sent_on = today
                nd = news_days.get(today)
                notifier.send(
                    "Gold bot: watching today 7:20–9:30 CT",
                    (f"News today: {nd} at 7:30 CT (no entries 7:00–8:00)\n" if nd else "News today: none\n")
                    + messages.status(res, symbol),
                )

            # stale-data watchdog (market open Sun 5pm - Fri 4pm CT, daily break 4-5pm)
            age_min = (time.time() * 1000 - (bars[-1].time + BAR_MS)) / 60000
            if _market_open() and age_min > 45 and not stale_warned:
                stale_warned = True
                notifier.send("Gold bot: no new candles", f"Last closed bar is {age_min:.0f} min old. Check the bot / connection.")
            elif age_min <= 45:
                stale_warned = False
    finally:
        stream.cancel()


def _event_key(ev, symbol: str) -> str:
    return f"{symbol}|{ev.kind}|{ev.bar_time}"


def _market_open() -> bool:
    n = datetime.now(CHICAGO)
    wd, hm = n.weekday(), n.hour * 100 + n.minute
    if wd == 5:  # Saturday
        return False
    if wd == 6:  # Sunday
        return hm >= 1700
    if wd == 4:  # Friday
        return hm < 1600
    return not (1600 <= hm < 1700)


# ───────────────────────── backtest output ─────────────────────────


def load_bars_csv(path: Path) -> list[Bar]:
    """CSV with columns time (epoch seconds or ISO), open, high, low, close, volume (TradingView export works)."""
    bars: list[Bar] = []
    with path.open() as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): v for k, v in row.items()}
            t = row["time"]
            ms = int(float(t) * 1000) if t.replace(".", "").isdigit() else int(datetime.fromisoformat(t).timestamp() * 1000)
            bars.append(
                Bar(ms, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row.get("volume") or 0))
            )
    return sorted(bars, key=lambda b: b.time)


def print_backtest(res, symbol: str, point_value: float) -> None:
    closed = [t for t in res.trades if not t.is_open]
    print(f"\n{symbol}: {len(res.bars)} bars, {len(res.trades)} trades ({len(closed)} closed)\n")
    print(f"{'#':>3}  {'signal bar (CT)':<22}{'side':<6}{'entry':>9}{'exit':>9}  {'reason':<7}{'pts':>7}{'$/contract':>12}")
    total = 0.0
    gross_win = gross_loss = 0.0
    for i, t in enumerate(res.trades, 1):
        when = datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO).strftime("%Y-%m-%d %H:%M")
        side = "LONG" if t.direction > 0 else "SHORT"
        if t.is_open:
            print(f"{i:>3}  {when:<22}{side:<6}{(t.entry_price or 0):>9.1f}{'':>9}  open")
            continue
        usd = (t.points or 0) * point_value
        total += usd
        gross_win += max(usd, 0)
        gross_loss += max(-usd, 0)
        print(f"{i:>3}  {when:<22}{side:<6}{t.entry_price:>9.1f}{t.exit_price:>9.1f}  {t.exit_reason:<7}{t.points:>7.1f}{usd:>12,.0f}")
    wins = sum(1 for t in closed if t.won)
    if closed:
        pf = gross_win / gross_loss if gross_loss else float("inf")
        print(
            f"\nWin rate {wins}/{len(closed)} = {100 * wins / len(closed):.1f}% · profit factor {pf:.2f} · "
            f"net ${total:,.0f} per contract before commission/slippage"
        )


def compare_with_tradingview(res, path: Path) -> None:
    """Match the bot's trades to TradingView's list (same direction, entry within 15 minutes)."""
    tv = []
    with path.open() as f:
        for row in csv.DictReader(f):
            tv.append((row["direction"], datetime.fromisoformat(row["entry_time_ct"]).replace(tzinfo=CHICAGO)))
    bot = [
        ("long" if t.direction > 0 else "short", datetime.fromtimestamp(t.signal_time / 1000 + 900, tz=CHICAGO))
        for t in res.trades
    ]
    start = datetime.fromtimestamp(res.bars[0].time / 1000, tz=CHICAGO) + timedelta(days=5)  # indicator warm-up
    end = datetime.fromtimestamp(res.bars[-1].time / 1000, tz=CHICAGO)
    tv = [x for x in tv if start <= x[1] <= end]
    matched = [x for x in tv if any(d == x[0] and abs((b - x[1]).total_seconds()) <= 900 for d, b in bot)]
    print(f"\nTradingView parity: {len(matched)}/{len(tv)} TradingView trades also taken by the bot")
    for d, t in tv:
        if (d, t) not in matched:
            print(f"  missing: {d} entry {t:%Y-%m-%d %H:%M} CT")


# ───────────────────────── entry point ─────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(prog="goldbot", description="Metals Confluence gold signal bot (tastytrade data)")
    ap.add_argument("-c", "--config", default="config.toml", help="path to config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="watch live candles and send alerts")
    r.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    sub.add_parser("check", help="connect, load history, print today's checklist")
    b = sub.add_parser("backtest", help="replay the strategy on past candles")
    b.add_argument("--days", type=int, default=120)
    b.add_argument("--csv", type=Path, help="use bars from a CSV file instead of tastytrade")
    b.add_argument("--compare", type=Path, help="TradingView trade list CSV to check parity against")
    sub.add_parser("test-notify", help="send a test notification")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = load(Path(a.config))
    if a.cmd == "run":
        asyncio.run(cmd_run(s, a.dry_run))
    elif a.cmd == "check":
        asyncio.run(cmd_check(s))
    elif a.cmd == "backtest":
        asyncio.run(cmd_backtest(s, a.days, a.compare, a.csv))
    elif a.cmd == "test-notify":
        Notifier(s.notify).send("Gold bot test", "If you can read this on your phone, alerts are set up.")


if __name__ == "__main__":
    main()
