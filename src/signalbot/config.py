"""Settings: config.toml for everything non-secret, .env / environment for broker credentials."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .notify import NotifyConfig


@dataclass
class Watch:
    """One strategy running on one instrument."""

    name: str
    strategy: str
    symbol: str = "auto"  # "auto" (+ product) = active futures month, "/GCZ6", or a stock ticker
    product: str = ""  # futures product code for "auto", e.g. "GC", "ES"
    interval: str = "15m"
    enabled: bool = True
    news_alert: bool = False  # heads-up at 7:00 CT on CPI / jobs days
    paper: bool = False  # alerts are marked PAPER: for watching / paper trading, not real money
    contracts: dict[str, float] = field(default_factory=dict)  # name -> $ per point, for alert lines
    point_value: float = 1.0  # $ per point of the data symbol, for backtest totals
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    watches: list[Watch] = field(default_factory=list)
    history_days: int = 30
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    news_days: dict[str, str] = field(default_factory=dict)
    fetch_news_calendar: bool = True
    heartbeat_hhmm: int = 715  # weekday "bot is running" message, Chicago time; -1 = off
    state_dir: Path = Path.home() / ".signalbot"

    def watch(self, name: str) -> Watch:
        for w in self.watches:
            if w.name == name:
                return w
        raise KeyError(f"no [[watch]] named {name!r}; have: {', '.join(w.name for w in self.watches)}")


def load_dotenv(path: Path) -> None:
    """Tiny .env reader (KEY=value lines)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _make(dc_type, data: dict, where: str):
    names = {f.name for f in fields(dc_type)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"unknown setting(s) in {where}: {', '.join(sorted(unknown))}")
    return dc_type(**data)


def load(config_path: Path) -> Settings:
    load_dotenv(config_path.parent / ".env")
    s = Settings()
    if not config_path.exists():
        raise FileNotFoundError(f"{config_path} not found - copy config.example.toml to config.toml")
    raw = tomllib.loads(config_path.read_text())
    for key in ("history_days", "fetch_news_calendar", "heartbeat_hhmm"):
        if key in raw:
            setattr(s, key, raw[key])
    if "state_dir" in raw:
        s.state_dir = Path(os.path.expanduser(raw["state_dir"]))
    s.notify = _make(NotifyConfig, raw.get("notify", {}), "[notify]")
    s.news_days = {str(k): str(v) for k, v in raw.get("news_days", {}).items()}
    for i, w in enumerate(raw.get("watch", [])):
        s.watches.append(_make(Watch, w, f"[[watch]] #{i + 1}"))
    names = [w.name for w in s.watches]
    if len(names) != len(set(names)):
        raise ValueError("each [[watch]] needs a unique name")
    return s
