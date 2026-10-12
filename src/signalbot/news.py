"""News days (CPI / jobs report at 7:30 CT) when the strategy skips 7:00-8:00 CT.

Two sources, merged:
1. ``news_days`` in config.toml ("YYYY-MM-DD" = "name"), the same list as the TradingView script;
2. optionally, this week's calendar from the public ForexFactory feed: high-impact USD
   events at 8:30 New York time whose title mentions CPI or Non-Farm payrolls.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
KEYWORDS = ("CPI", "Non-Farm", "Nonfarm", "NFP")
CHICAGO = ZoneInfo("America/Chicago")


def from_config(entries: dict[str, str]) -> dict[date, str]:
    out: dict[date, str] = {}
    for k, v in entries.items():
        try:
            out[date.fromisoformat(k)] = v
        except ValueError:
            log.warning("ignoring bad news date %r", k)
    return out


def parse_ff(events: list[dict]) -> dict[date, str]:
    """Pick US CPI / payrolls releases at 7:30 CT out of a ForexFactory weekly feed."""
    out: dict[date, str] = {}
    for e in events:
        if e.get("country") != "USD" or e.get("impact") != "High":
            continue
        title = str(e.get("title", ""))
        if not any(k.lower() in title.lower() for k in KEYWORDS):
            continue
        try:
            when = datetime.fromisoformat(str(e["date"])).astimezone(CHICAGO)
        except (KeyError, ValueError):
            continue
        if (when.hour, when.minute) != (7, 30):
            continue
        name = "Jobs report" if "farm" in title.lower() or "nfp" in title.lower() else "CPI"
        out.setdefault(when.date(), name)
    return out


def fetch_ff(timeout: float = 15.0) -> dict[date, str]:
    """This week's CPI / jobs dates from ForexFactory; empty on any error (never blocks trading)."""
    try:
        req = urllib.request.Request(FF_URL, headers={"User-Agent": "signal-bot/0.2"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_ff(json.load(r))
    except Exception as exc:  # the feed is rate-limited and occasionally down
        log.warning("news calendar fetch failed: %s", exc)
        return {}
