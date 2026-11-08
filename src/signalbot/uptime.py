"""Knowing when the bot was NOT running.

Two independent halves, because a process cannot alert while it is dead:

* ``touch`` writes the time to ``state_dir/last_seen`` every minute. On the next start-up
  ``gap_since`` reads it back, so the bot can say "I was off from 6:05 PM to 7:12 AM and the
  market was open for 11 of those hours" - the answer to "why did I get no alerts last night".
* ``ping`` pokes an outside watchdog URL (healthchecks.io or anything that expects a regular
  GET). That one can shout while the machine is asleep, which the bot itself never can.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .bars import CHICAGO
from .net import urlopen

log = logging.getLogger(__name__)

_FILE = "last_seen"
STEP_MIN = 5  # resolution used when measuring how much of a gap was in market hours


def touch(state_dir, when: float | None = None) -> None:
    """Record that the bot is alive now. Cheap enough to call every minute."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / _FILE).write_text(f"{when or time.time():.0f}\n")
    except OSError as exc:  # a full or read-only disk must not stop the bot
        log.debug("could not write %s: %s", _FILE, exc)


def last_seen(state_dir) -> float | None:
    try:
        return float((state_dir / _FILE).read_text().strip())
    except (OSError, ValueError):
        return None


def open_minutes(start: datetime, end: datetime, is_open) -> int:
    """Roughly how many minutes between start and end the market was open, sampled every STEP_MIN."""
    n, t = 0, start
    while t < end:
        if is_open(t):
            n += STEP_MIN
        t += timedelta(minutes=STEP_MIN)
    return min(n, int((end - start).total_seconds() // 60))


def gap_since(state_dir, is_open, min_minutes: int = 20, now: float | None = None) -> tuple[str, str] | None:
    """(title, body) if the bot was off long enough to have missed something, else None.

    ``is_open(datetime) -> bool`` says whether the market was trading - pass runner.market_open.
    Returns None on a first ever run, a short restart, or a gap entirely outside market hours.
    """
    seen = last_seen(state_dir)
    now = now or time.time()
    if seen is None or now - seen < min_minutes * 60:
        return None
    start = datetime.fromtimestamp(seen, CHICAGO)
    end = datetime.fromtimestamp(now, CHICAGO)
    missed = open_minutes(start, end, is_open)
    if missed < min_minutes:
        return None  # it was off while the market was shut: nothing was missed
    down = int((now - seen) // 60)

    def when(d):  # %-I is POSIX-only; Windows wants %#I, so build it from the fields instead
        t = f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"
        return t if start.date() == end.date() else f"{d:%a} {t}"
    span = f"{down // 60}h {down % 60}m" if down >= 60 else f"{down} min"
    title = f"Signal bot was offline for {span}"
    body = (
        f"No alerts between {when(start)} and {when(end)} CT.\n\n"
        f"The market was open for about {missed // 60}h {missed % 60}m of that, so signals in that "
        f"window never reached you. Open trades that are still live will be alerted now; "
        f"ones that opened and closed while the bot was off stay silent.\n\n"
        f"Usual cause: the machine slept. Keep it plugged in and awake, or move the bot to one "
        f"that stays on."
    )
    return title, body


def ping(url: str, timeout: float = 5.0) -> bool:
    """Tell an outside watchdog the bot is alive. Never raises - a missed ping is not an outage."""
    if not url:
        return False
    try:
        urlopen(url, timeout=timeout).close()
        return True
    except Exception as exc:
        log.debug("watchdog ping failed: %s", exc)
        return False
