"""Live and historical candles from tastytrade (DXLink stream) for futures and stocks."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from tastytrade import DXLinkStreamer, Session
from tastytrade.dxfeed import Candle
from tastytrade.instruments import Future

from ..bars import Bar, interval_ms

log = logging.getLogger(__name__)


@dataclass
class Instrument:
    symbol: str  # what you trade / see, e.g. "/GCZ6" or "SPY"
    streamer_symbol: str  # what DXLink wants, e.g. "/GCZ26:XCEC"
    expires: date | None = None


def make_session() -> Session:
    """Reads TT_SECRET and TT_REFRESH from the environment (.env is loaded by config)."""
    import os

    secret, refresh = os.environ.get("TT_SECRET", ""), os.environ.get("TT_REFRESH", "")
    if not secret or secret.startswith("your-"):
        raise SystemExit("TT_SECRET is not set in .env (the OAuth app's Client Secret)")
    if not refresh.startswith("eyJ"):
        raise SystemExit(
            "TT_REFRESH in .env doesn't look like a refresh token. It is a long text starting with 'eyJ', "
            "shown after Manage -> Create Grant on the tastytrade API page (not the Client ID or Client Secret)."
        )
    return Session(secret, refresh)


async def resolve(session: Session, symbol: str, product: str = "") -> Instrument:
    """Futures: symbol "auto" + product ("GC", "ES", ...) picks the active month; or give "/GCZ6".
    Stocks/ETFs: give the ticker ("SPY")."""
    if symbol.lower() == "auto":
        if not product:
            raise ValueError("symbol = 'auto' needs product, e.g. product = 'GC'")
        futures = await Future.get(session, product_codes=[product])
        live = [f for f in futures if not f.is_closing_only]
        active = [f for f in live if f.active_month] or sorted(live, key=lambda f: f.expiration_date)
        if not active:
            raise RuntimeError(f"No tradeable {product} futures returned by tastytrade")
        f = active[0]
        return Instrument(f.symbol, f.streamer_symbol, f.expiration_date)
    if symbol.startswith("/"):
        f = await Future.get(session, symbol)
        return Instrument(f.symbol, f.streamer_symbol, f.expiration_date)
    return Instrument(symbol.upper(), symbol.upper())


def _to_bar(c: Candle) -> Bar | None:
    if not c.time or c.open is None or c.close is None:
        return None
    o, h, lo, cl = float(c.open), float(c.high), float(c.low), float(c.close)
    if 0 in (o, h, lo, cl):
        return None  # empty placeholder candles
    return Bar(time=int(c.time), open=o, high=h, low=lo, close=cl, volume=float(c.volume or 0))


class CandleBook:
    """Latest version of every candle for one symbol + interval, keyed by open time."""

    def __init__(self, interval: str, keep_days: int = 40) -> None:
        self.interval = interval
        self.bar_ms = interval_ms(interval)
        self.keep_ms = keep_days * 86_400_000
        self._bars: dict[int, Bar] = {}
        self.ready = asyncio.Event()  # set once the history snapshot has fully arrived
        self.last_update = 0.0  # time.monotonic() of the latest candle event

    def apply(self, c: Candle) -> None:
        self.last_update = time.monotonic()
        if c.remove:
            self._bars.pop(int(c.time), None)
        else:
            bar = _to_bar(c)
            if bar is not None:
                self._bars[bar.time] = bar
        if (c.snapshot_end or c.snapshot_snip) and not c.pending:
            self.ready.set()
            self._prune()

    def _prune(self) -> None:
        """Drop bars older than keep_days so weeks of uptime don't grow memory."""
        if not self._bars:
            return
        cutoff = max(self._bars) - self.keep_ms
        for t in [t for t in self._bars if t < cutoff]:
            del self._bars[t]

    def closed_bars(self, now_ms: int | None = None) -> list[Bar]:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        if len(self._bars) > 5000:
            self._prune()
        return [self._bars[t] for t in sorted(self._bars) if t + self.bar_ms <= now_ms]

    def __len__(self) -> int:
        return len(self._bars)


async def _subscribe(streamer: DXLinkStreamer, inst: Instrument, interval: str, days: int) -> None:
    start = datetime.now(timezone.utc) - timedelta(days=days)
    # extended hours on: keep overnight bars, like TradingView's default futures chart
    await streamer.subscribe_candle([inst.streamer_symbol], interval, start_time=start, extended_trading_hours=True)


async def fetch_history(session: Session, inst: Instrument, interval: str, days: int, quiet_secs: float = 4.0) -> list[Bar]:
    """Download the last ``days`` of candles and return the closed ones."""
    book = CandleBook(interval)
    async with DXLinkStreamer(session) as streamer:
        await _subscribe(streamer, inst, interval, days)
        while True:
            try:
                c = await asyncio.wait_for(streamer.get_event(Candle), timeout=quiet_secs)
            except TimeoutError:
                break  # quiet for a while: the snapshot is complete
            book.apply(c)
            if (c.snapshot_end or c.snapshot_snip) and not c.pending:
                quiet_secs = 1.0  # drain what's queued, then stop
    bars = book.closed_bars()
    log.info("history: %d closed %s bars for %s", len(bars), interval, inst.symbol)
    return bars


async def stream_forever(session: Session, inst: Instrument, interval: str, days: int, book: CandleBook) -> None:
    """Keep ``book`` updated from the live stream; reconnect with backoff on errors."""
    delay = 5
    while True:
        try:
            async with DXLinkStreamer(session) as streamer:
                await _subscribe(streamer, inst, interval, days)
                delay = 5
                async for c in streamer.listen(Candle):
                    book.apply(c)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("%s stream dropped (%s); reconnecting in %ss", inst.symbol, exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
