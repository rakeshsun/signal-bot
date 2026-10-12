"""Settings: config.toml for everything non-secret, .env / environment for tastytrade credentials."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .notify import NotifyConfig
from .strategy import Params


@dataclass
class Settings:
    symbol: str = "auto"  # "auto" = active GC month, or e.g. "/GCZ6"
    product: str = "GC"
    history_days: int = 30  # warm-up history; 200-EMA needs ~3 trading days of 15m bars
    params: Params = field(default_factory=Params)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    news_days: dict[str, str] = field(default_factory=dict)
    fetch_news_calendar: bool = True
    heartbeat_hhmm: int = 715  # daily "bot is running" message, Chicago time; -1 = off
    state_dir: Path = Path.home() / ".goldbot"


def load_dotenv(path: Path) -> None:
    """Tiny .env reader (KEY=value lines) so we don't need python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fill(dc_type, data: dict):
    names = {f.name for f in fields(dc_type)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"Unknown setting(s) in [{dc_type.__name__}]: {', '.join(sorted(unknown))}")
    return dc_type(**data)


def load(config_path: Path) -> Settings:
    load_dotenv(config_path.parent / ".env")
    s = Settings()
    if not config_path.exists():
        return s
    raw = tomllib.loads(config_path.read_text())
    for key in ("symbol", "product", "history_days", "fetch_news_calendar", "heartbeat_hhmm"):
        if key in raw:
            setattr(s, key, raw[key])
    if "state_dir" in raw:
        s.state_dir = Path(os.path.expanduser(raw["state_dir"]))
    s.params = _fill(Params, raw.get("strategy", {}))
    s.notify = _fill(NotifyConfig, raw.get("notify", {}))
    s.news_days = {str(k): str(v) for k, v in raw.get("news_days", {}).items()}
    return s
