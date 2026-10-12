"""Live loop: one candle stream per watch, evaluate each strategy when a new bar closes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import messages, news, strategies
from .bars import CHICAGO, hhmm
from .config import Settings, Watch
from .engine import Result, simulate
from .notify import Notifier
from .strategies.base import Context, Strategy

log = logging.getLogger(__name__)
SETTLE_SECS = 20  # after a bar closes, wait so its final update has arrived


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
        self.path.write_text(json.dumps(sorted(self.keys)[-5000:]))
        return True


def news_days(s: Settings, fetch: bool) -> dict[date, str]:
    days = news.from_config(s.news_days)
    if fetch and s.fetch_news_calendar:
        for d, name in news.fetch_ff().items():
            days.setdefault(d, name)
    return days


def evaluate(strategy: Strategy, bars, ctx: Context) -> Result:
    return simulate(bars, strategy.signals(bars, ctx), flat_hhmm=getattr(strategy, "flat_hhmm", None))


@dataclass
class Live:
    watch: Watch
    strategy: Strategy
    symbol: str
    book: object
    task: asyncio.Task
    last_bar: int = 0
    res: Result | None = None


def market_open(now: datetime | None = None) -> bool:
    """CME Globex hours: Sun 5pm - Fri 4pm CT with a 4-5pm daily break."""
    n = now or datetime.now(CHICAGO)
    wd, hm = n.weekday(), n.hour * 100 + n.minute
    if wd == 5:
        return False
    if wd == 6:
        return hm >= 1700
    if wd == 4:
        return hm < 1600
    return not (1600 <= hm < 1700)


async def run_live(s: Settings, dry_run: bool = False) -> None:
    from .data import tastytrade as tt

    watches = [w for w in s.watches if w.enabled]
    if not watches:
        raise SystemExit("No enabled [[watch]] in config.toml")
    notifier = Notifier(s.notify, dry_run=dry_run)
    sent = SentLog(s.state_dir / "sent.json")
    session = tt.make_session()
    nd = news_days(s, fetch=True)
    nd_on = datetime.now(CHICAGO).date()

    lives: list[Live] = []
    for w in watches:
        strat_cls = strategies.get(w.strategy)
        strat = strat_cls(strat_cls.make_params(w.params))
        inst = await tt.resolve(session, w.symbol, w.product)
        book = tt.CandleBook(w.interval)
        task = asyncio.create_task(tt.stream_forever(session, inst, w.interval, s.history_days, book))
        lives.append(Live(w, strat, inst.symbol, book, task))
        log.info("%s: %s on %s (%s)", w.name, w.strategy, inst.symbol, w.interval)

    # wait for history, then mark everything that already happened as sent
    t0 = time.monotonic()
    while any(len(lv.book) < 300 for lv in lives) and time.monotonic() - t0 < 120:
        await asyncio.sleep(2)
    start_msgs = []
    for lv in lives:
        bars = lv.book.closed_bars()
        ctx = Context(lv.symbol, nd)
        lv.res = evaluate(lv.strategy, bars, ctx)
        lv.last_bar = bars[-1].time if bars else 0
        for ev in lv.res.events:
            sent.add(f"{lv.watch.name}|{ev.kind}|{ev.bar_time}")
        start_msgs.append(messages.status(lv.watch, lv.symbol, lv.res, lv.strategy.checklist(bars, ctx)))
    notifier.send("Signal bot started", "\n\n".join(start_msgs))

    heartbeat_on: date | None = None
    news_alerted: set[date] = set()
    stale_warned: set[str] = set()
    try:
        while True:
            now = time.time()
            await asyncio.sleep(((SETTLE_SECS - now % 60) % 60) or 60)  # every minute at :20 s
            for lv in lives:
                if lv.task.done():
                    raise RuntimeError(f"{lv.watch.name} stream stopped") from lv.task.exception()

            today = datetime.now(CHICAGO).date()
            if today != nd_on:
                nd, nd_on = news_days(s, fetch=True), today

            # news heads-up at 7:00 CT
            now_hm = hhmm(int(time.time() * 1000))
            if 700 <= now_hm < 730 and today in nd and today not in news_alerted and any(lv.watch.news_alert for lv in lives):
                news_alerted.add(today)
                if sent.add(f"news|{today}"):
                    notifier.send(*messages.news(nd[today]))

            for lv in lives:
                bars = lv.book.closed_bars()
                if not bars or bars[-1].time <= lv.last_bar:
                    continue
                lv.last_bar = bars[-1].time
                ctx = Context(lv.symbol, nd)
                lv.res = evaluate(lv.strategy, bars, ctx)
                for ev in lv.res.events:
                    if ev.bar_index < len(bars) - 2:
                        continue
                    if not sent.add(f"{lv.watch.name}|{ev.kind}|{ev.bar_time}"):
                        continue
                    if ev.kind == "entry":
                        title, body = messages.entry(ev.trade, lv.watch, lv.symbol, lv.res, lv.book.bar_ms)
                        notifier.send(title, body, urgent=True)
                    else:
                        notifier.send(*messages.exit_(ev.trade, lv.watch, lv.symbol))

            # weekday heartbeat
            if s.heartbeat_hhmm >= 0 and now_hm >= s.heartbeat_hhmm and heartbeat_on != today and today.weekday() < 5:
                heartbeat_on = today
                head = f"News today: {nd[today]} at 7:30 CT" if today in nd else "News today: none"
                parts = [head]
                for lv in lives:
                    bars = lv.book.closed_bars()
                    if lv.res:
                        parts.append(messages.status(lv.watch, lv.symbol, lv.res, lv.strategy.checklist(bars, Context(lv.symbol, nd))))
                notifier.send("Signal bot: running", "\n\n".join(parts))

            # stale data watchdog
            if market_open():
                for lv in lives:
                    bars = lv.book.closed_bars()
                    age = (time.time() * 1000 - (bars[-1].time + lv.book.bar_ms)) / 60000 if bars else 1e9
                    if age > 45 and lv.watch.name not in stale_warned:
                        stale_warned.add(lv.watch.name)
                        notifier.send(f"{lv.watch.name}: no new candles", f"Last closed bar is {age:.0f} min old. Check the bot / connection.")
                    elif age <= 45:
                        stale_warned.discard(lv.watch.name)
    finally:
        for lv in lives:
            lv.task.cancel()
