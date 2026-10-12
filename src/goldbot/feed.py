"""Live and historical 15-minute gold futures candles from tastytrade (DXLink stream)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from tastytrade import DXLinkStreamer, Session
from tastytrade.dxfeed import Candle
from tastytrade.instruments import Future

from .strategy import Bar

log = logging.getLogger(__name__)

BAR_MS = 15 * 60 * 1000


async def resolve_future(session: Session, symbol: str, product: str = "GC") -> Future:
    """Return the future to watch: an explicit symbol like '/GCZ6', or 'auto' for the active month."""
    if symbol and symbol.lower() != "auto":
        return await Future.get(session, symbol)
    futures = await Future.get(session, product_codes=[product])
    active = [f for f in futures if f.active_month and not f.is_closing_only]
    if not active:
        active = sorted((f for f in futures if not f.is_closing_only), key=lambda f: f.expiration_date)
    if not active:
        raise RuntimeError(f"No tradeable {product} futures returned by tastytrade")
    return active[0]


def _to_bar(c: Candle) -> Bar | None:
    if not c.time or c.open is None or c.close is None:
        return None
    o, h, lo, cl = float(c.open), float(c.high), float(c.low), float(c.close)
    if o == 0 or h == 0 or lo == 0 or cl == 0:
        return None  # dxfeed sends empty placeholder candles with zero prices
    return Bar(time=int(c.time), open=o, high=h, low=lo, close=cl, volume=float(c.volume or 0))


class CandleBook:
    """Keeps the latest version of every candle, keyed by its open time."""

    def __init__(self) -> None:
        self._bars: dict[int, Bar] = {}
        self.last_update = 0.0

    def apply(self, c: Candle) -> None:
        if c.remove:
            self._bars.pop(int(c.time), None)
            return
        bar = _to_bar(c)
        if bar is not None:
            self._bars[bar.time] = bar
            self.last_update = time.monotonic()

    def closed_bars(self, now_ms: int | None = None) -> list[Bar]:
        """Bars whose 15 minutes are over, oldest first."""
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        return [self._bars[t] for t in sorted(self._bars) if t + BAR_MS <= now_ms]

    def __len__(self) -> int:
        return len(self._bars)


async def _subscribe(streamer: DXLinkStreamer, streamer_symbol: str, days: int) -> None:
    start = datetime.now(timezone.utc) - timedelta(days=days)
    # extended_trading_hours=True: keep the overnight bars, like TradingView's default futures chart
    await streamer.subscribe_candle([streamer_symbol], "15m", start_time=start, extended_trading_hours=True)


async def fetch_history(session: Session, streamer_symbol: str, days: int, quiet_secs: float = 4.0) -> list[Bar]:
    """Download the last ``days`` of 15-minute candles and return the closed ones."""
    book = CandleBook()
    async with DXLinkStreamer(session) as streamer:
        await _subscribe(streamer, streamer_symbol, days)
        got_end = False
        while True:
            try:
                c = await asyncio.wait_for(streamer.get_event(Candle), timeout=quiet_secs)
            except TimeoutError:
                break  # nothing new for a while: the snapshot is done
            book.apply(c)
            if c.snapshot_end:
                got_end = True
            if got_end and not c.pending:
                # drain anything already queued, then stop
                quiet_secs = 1.0
    bars = book.closed_bars()
    log.info("history: %d closed bars for %s", len(bars), streamer_symbol)
    return bars


async def stream_forever(
    session: Session,
    streamer_symbol: str,
    days: int,
    book: CandleBook,
    on_connected: Callable[[], None] | None = None,
) -> None:
    """Keep ``book`` updated from the live candle stream; reconnect with backoff on errors."""
    delay = 5
    while True:
        try:
            async with DXLinkStreamer(session) as streamer:
                await _subscribe(streamer, streamer_symbol, days)
                if on_connected:
                    on_connected()
                delay = 5
                async for c in streamer.listen(Candle):
                    book.apply(c)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network drops, token refresh hiccups, server restarts
            log.warning("candle stream dropped (%s); reconnecting in %ss", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
