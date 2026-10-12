"""Live loop: one candle stream per watch, evaluate each strategy when a new bar closes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import dashboard, messages, news, strategies
from .bars import CHICAGO, hhmm
from .config import Settings, Watch
from .engine import Result, simulate
from .notify import Notifier
from .strategies.base import Context, Strategy

log = logging.getLogger(__name__)
SETTLE_SECS = 20  # after a bar closes, wait so its final update has arrived
STALE_MIN = 45  # minutes without a new closed bar (while the market is open) before we act
KEEP_SENT_DAYS = 120


class SentLog:
    """Alerts that already went out ({key: unix time}), so a restart never repeats them."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            raw = json.loads(path.read_text())
            # older versions stored a plain list of keys
            self.keys: dict[str, float] = {k: time.time() for k in raw} if isinstance(raw, list) else dict(raw)
        except Exception:
            self.keys = {}

    def __contains__(self, key: str) -> bool:
        return key in self.keys

    def add(self, key: str) -> bool:
        if key in self.keys:
            return False
        self.keys[key] = time.time()
        cutoff = time.time() - KEEP_SENT_DAYS * 86400
        self.keys = {k: t for k, t in self.keys.items() if t >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.keys))
        os.replace(tmp, self.path)  # atomic: a crash mid-write can't corrupt the file
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
    inst: object
    book: object
    task: asyncio.Task
    last_bar: int = 0
    res: Result | None = None
    stale_since: float | None = None
    restarts: int = 0
    extra: dict = field(default_factory=dict)


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


class Alerts:
    """Sends alerts once: dedup via SentLog, mark as sent only after delivery, keep a history."""

    def __init__(self, notifier: Notifier, sent: SentLog, history_path: Path) -> None:
        self.notifier, self.sent, self.path = notifier, sent, history_path
        self.recent: deque[dict] = deque(maxlen=200)
        try:
            for line in history_path.read_text().splitlines()[-200:]:
                self.recent.append(json.loads(line))
        except Exception:
            pass

    async def send(self, key: str | None, title: str, body: str, urgent: bool = False, kind: str = "info") -> bool:
        if key is not None and key in self.sent:
            return False
        ok = await asyncio.to_thread(self.notifier.send, title, body, urgent)
        if not ok:
            log.warning("alert not delivered, will retry next minute: %s", title)
            return False
        if key is not None:
            self.sent.add(key)
        item = {"t": time.time(), "kind": kind, "title": title, "body": body}
        self.recent.append(item)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(item) + "\n")
            lines = self.path.read_text().splitlines()
            if len(lines) > 1000:  # keep the history file small
                self.path.write_text("\n".join(lines[-500:]) + "\n")
        except Exception as exc:
            log.warning("could not save alert history: %s", exc)
        return True


async def run_live(s: Settings, dry_run: bool = False, dashboard_path: Path | None = None) -> None:
    notifier = Notifier(s.notify, dry_run=dry_run)
    sent = SentLog(s.state_dir / "sent.json")
    try:
        await _run(s, notifier, sent, dashboard_path)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except BaseException as exc:
        # launchd restarts us; tell the phone once a day so a broken key or network isn't silent
        if not isinstance(exc, SystemExit) or exc.code not in (0, None):
            key = f"crash|{date.today()}"
            if key not in sent and await asyncio.to_thread(
                notifier.send, "Signal bot is failing", f"{type(exc).__name__}: {str(exc)[:300]}\nCheck logs/crash.log", True
            ):
                sent.add(key)
        raise


async def _start_watch(s: Settings, session, w: Watch) -> Live:
    from .data import tastytrade as tt

    strat_cls = strategies.get(w.strategy)
    strat = strat_cls(strat_cls.make_params(w.params))
    inst = await tt.resolve(session, w.symbol, w.product)
    book = tt.CandleBook(w.interval, keep_days=s.history_days + 10)
    task = asyncio.create_task(tt.stream_forever(session, inst, w.interval, s.history_days, book))
    log.info("%s: %s on %s (%s)", w.name, w.strategy, inst.symbol, w.interval)
    return Live(w, strat, inst.symbol, inst, book, task)


async def _baseline(lv: Live, nd: dict[date, str], sent: SentLog) -> None:
    """After (re)start: everything before the last closed bar counts as already handled; the
    last bar is left to the loop, so a restart right after a signal still delivers it."""
    try:
        await asyncio.wait_for(lv.book.ready.wait(), timeout=180)
    except TimeoutError:
        log.warning("%s: history snapshot incomplete after 180s, continuing with %d bars", lv.watch.name, len(lv.book))
    bars = lv.book.closed_bars()
    lv.res = evaluate(lv.strategy, bars, Context(lv.symbol, nd))
    last = bars[-1].time if bars else 0
    for ev in lv.res.events:
        if ev.bar_time < last:
            sent.add(f"{lv.watch.name}|{ev.kind}|{ev.bar_time}")
    lv.last_bar = bars[-2].time if len(bars) > 1 else 0


async def _run(s: Settings, notifier: Notifier, sent: SentLog, dashboard_path: Path | None) -> None:
    from .data import tastytrade as tt

    watches = [w for w in s.watches if w.enabled]
    if not watches:
        raise SystemExit("No enabled [[watch]] in config.toml")
    alerts = Alerts(notifier, sent, s.state_dir / "alerts.jsonl")
    session = tt.make_session()
    nd = await asyncio.to_thread(news_days, s, True)
    nd_on = datetime.now(CHICAGO).date()

    lives = [await _start_watch(s, session, w) for w in watches]
    for lv in lives:
        await _baseline(lv, nd, sent)
    started = time.time()
    log.info("started:\n%s", "\n\n".join(
        messages.status(lv.watch, lv.symbol, lv.res, lv.strategy.checklist(lv.book.closed_bars(), Context(lv.symbol, nd)))
        for lv in lives))
    if dashboard_path is not None:
        dashboard.write(dashboard_path, lives, nd, list(alerts.recent), started, market_open())
        log.info("dashboard: %s", dashboard_path)

    try:
        while True:
            now = time.time()
            await asyncio.sleep(((SETTLE_SECS - now % 60) % 60) or 60)  # every minute at :20 s
            now_ct = datetime.now(CHICAGO)
            today, now_hm = now_ct.date(), hhmm(int(time.time() * 1000))

            for i, lv in enumerate(lives):
                if lv.task.done():
                    exc = None if lv.task.cancelled() else lv.task.exception()
                    log.warning("%s stream task ended (%s); restarting it", lv.watch.name, exc)
                    lv.task = asyncio.create_task(tt.stream_forever(session, lv.inst, lv.watch.interval, s.history_days, lv.book))

            # once a day: refresh the news calendar and follow futures contract rolls ("auto")
            if today != nd_on:
                nd, nd_on = await asyncio.to_thread(news_days, s, True), today
                for i, lv in enumerate(lives):
                    if lv.watch.symbol.lower() != "auto":
                        continue
                    try:
                        inst = await tt.resolve(session, lv.watch.symbol, lv.watch.product)
                    except Exception as exc:
                        log.warning("%s: could not re-check active contract: %s", lv.watch.name, exc)
                        continue
                    if inst.symbol != lv.symbol:
                        log.info("%s: contract roll %s -> %s", lv.watch.name, lv.symbol, inst.symbol)
                        lv.task.cancel()
                        new = await _start_watch(s, session, lv.watch)
                        await _baseline(new, nd, sent)
                        lives[i] = new
                        await alerts.send(f"roll|{lv.watch.name}|{inst.symbol}", f"{lv.watch.name}: now watching {inst.symbol}",
                                          f"Active contract rolled from {lv.symbol} to {inst.symbol}.", kind="info")

            # news heads-up at 7:00 CT
            if 700 <= now_hm < 730 and today in nd and any(lv.watch.news_alert for lv in lives):
                await alerts.send(f"news|{today}", *messages.news(nd[today]), kind="news")

            # new closed bars -> strategy -> alerts
            for lv in lives:
                bars = lv.book.closed_bars()
                if not bars:
                    continue
                if bars[-1].time > lv.last_bar:
                    lv.last_bar = bars[-1].time
                    lv.res = evaluate(lv.strategy, bars, Context(lv.symbol, nd))
                if lv.res is None:
                    continue
                n = len(lv.res.bars)
                for ev in lv.res.events:
                    key = f"{lv.watch.name}|{ev.kind}|{ev.bar_time}"
                    if key in sent:
                        continue
                    if ev.kind == "entry":
                        if ev.bar_index < n - 2:  # an entry older than one bar is no longer actionable
                            continue
                        title, body = messages.entry(ev.trade, lv.watch, lv.symbol, lv.res, lv.book.bar_ms)
                        await alerts.send(key, title, body, urgent=True, kind="entry")
                    else:  # always report exits, even late ones after a data gap
                        title, body = messages.exit_(ev.trade, lv.watch, lv.symbol)
                        if ev.bar_index < n - 2:
                            title += " (late)"
                        await alerts.send(key, title, body, kind="exit")

            # weekday heartbeat, once, inside a 30-minute window after heartbeat_hhmm
            hb_end = s.heartbeat_hhmm + 30 if s.heartbeat_hhmm % 100 < 30 else s.heartbeat_hhmm + 70
            if s.heartbeat_hhmm >= 0 and s.heartbeat_hhmm <= now_hm < hb_end and today.weekday() < 5:
                head = f"News today: {nd[today]} at 7:30 CT" if today in nd else "News today: none"
                parts = [head] + [
                    messages.status(lv.watch, lv.symbol, lv.res, lv.strategy.checklist(lv.book.closed_bars(), Context(lv.symbol, nd)))
                    for lv in lives if lv.res
                ]
                await alerts.send(f"heartbeat|{today}", "Signal bot: running", "\n\n".join(parts), kind="heartbeat")

            # stale data: only when the market has been open for a while (not right after 5pm CT)
            if market_open(now_ct) and market_open(now_ct - timedelta(minutes=STALE_MIN + 5)):
                for lv in lives:
                    bars = lv.book.closed_bars()
                    age = (time.time() * 1000 - (bars[-1].time + lv.book.bar_ms)) / 60000 if bars else 1e9
                    if age <= STALE_MIN:
                        lv.stale_since = None
                        continue
                    if lv.stale_since is None:
                        lv.stale_since = time.time()
                        log.warning("%s: no new candles for %.0f min; restarting the stream", lv.watch.name, age)
                        lv.task.cancel()  # a half-open socket never errors on its own
                        lv.restarts += 1
                        lv.task = asyncio.create_task(tt.stream_forever(session, lv.inst, lv.watch.interval, s.history_days, lv.book))
                    elif time.time() - lv.stale_since > 10 * 60:
                        await alerts.send(f"stale|{lv.watch.name}|{today}|{now_ct.hour}", f"{lv.watch.name}: no new candles",
                                          f"Last closed bar is {age:.0f} min old even after reconnecting. Check the bot / internet.",
                                          kind="warning")
            else:
                for lv in lives:
                    lv.stale_since = None

            if dashboard_path is not None:
                try:
                    dashboard.write(dashboard_path, lives, nd, list(alerts.recent), started, market_open(now_ct))
                except Exception as exc:
                    log.warning("dashboard update failed: %s", exc)
    finally:
        for lv in lives:
            lv.task.cancel()
