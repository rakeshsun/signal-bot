"""What a strategy plug-in looks like.

A strategy is a class with:

* ``name`` - the id used in config.toml (``strategy = "metals_confluence"``);
* ``Params`` - a dataclass of its settings (defaults = the tested values);
  config.toml's ``[watch.params]`` overrides any of them;
* ``signals(bars, ctx)`` - one entry per bar: ``None`` or a :class:`Signal`
  (direction, stop, target), evaluated on that bar's close;
* optionally ``checklist(bars, ctx)`` - rows shown by ``signalbot check`` and in
  the daily message, and ``flat_hhmm`` - a clock time to close open trades.

Fills, stops, targets, one-trade-at-a-time and alerts are handled by the engine
and runner, so a strategy file only contains its entry rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar

from ..bars import Bar
from ..engine import Signal


@dataclass
class Context:
    symbol: str = ""
    news_days: dict[date, str] = field(default_factory=dict)


# (label, long ok, short ok); use None for a side that doesn't apply, or a str for a value
ChecklistRow = tuple[str, Any, Any]


class Strategy(ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""

    @dataclass
    class Params:  # override in subclasses
        pass

    #: close open trades at the next open after a bar starting at/after this HHMM (Chicago); None = hold
    flat_hhmm: ClassVar[int | None] = None

    def __init__(self, params: Any | None = None) -> None:
        self.p = params if params is not None else self.Params()

    def summary(self, bars: list[Bar], ctx: Context, live: Bar | None = None) -> list[tuple[str, str, bool]]:
        """Plain-language status for the dashboard: (label, text, highlight). ``bars`` are closed bars,
        ``live`` the still-forming bar (or None). Default: nothing - the dashboard shows the checklist."""
        return []

    @classmethod
    def make_params(cls, overrides: dict[str, Any]) -> Any:
        valid = set(cls.Params.__dataclass_fields__)
        unknown = set(overrides) - valid
        if unknown:
            raise ValueError(
                f"{cls.name}: unknown param(s) {', '.join(sorted(unknown))}. Valid: {', '.join(sorted(valid))}"
            )
        return cls.Params(**overrides)

    @abstractmethod
    def signals(self, bars: list[Bar], ctx: Context) -> list[Signal | None]:
        """One entry per bar (None = no trade). Look only at bars[0..i] for bar i."""

    def checklist(self, bars: list[Bar], ctx: Context) -> list[ChecklistRow]:
        """Rows describing the latest bar, for status messages. Optional."""
        return []


def clock(hhmm: int) -> str:
    """720 -> '7:20 AM'."""
    h, m = divmod(int(hhmm), 100)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def now_hhmm() -> int:
    from datetime import datetime

    from ..bars import CHICAGO

    d = datetime.now(CHICAGO)
    return d.hour * 100 + d.minute


def in_window(t: int, start: int, end: int) -> bool:
    return (start <= t < end) if start <= end else (t >= start or t < end)
