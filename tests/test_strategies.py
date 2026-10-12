import json
from datetime import datetime, timedelta
from pathlib import Path

from signalbot import strategies
from signalbot.bars import CHICAGO
from signalbot.config import load
from signalbot.runner import evaluate
from signalbot.strategies.base import Context

from helpers import make_bars, random_walk

ROOT = Path(__file__).resolve().parents[1]


def test_registry_lists_builtin_strategies():
    names = set(strategies.available())
    assert {"metals_confluence", "es_range_breakout"} <= names
    assert "ema_cross_example" not in names  # _template.py is not loaded


def test_metals_matches_pre_refactor_results():
    """Locks the gold strategy to the results of the original single-strategy version."""
    expected = json.loads((ROOT / "tests/fixtures/metals_regression.json").read_text())
    cls = strategies.get("metals_confluence")
    for seed, exp in zip((7, 11, 23), expected):
        c, v = random_walk(seed=seed)
        res = evaluate(cls(), make_bars(c, vol=v), Context())
        got = [[t.direction, t.signal_index, t.entry_price, t.exit_index, t.exit_price, t.exit_reason] for t in res.trades]
        assert got == exp


def test_metals_session_and_news_rules():
    cls = strategies.get("metals_confluence")
    c, v = random_walk()
    bars = make_bars(c, vol=v)
    days = {bars[0].dt.date() + timedelta(days=d): "CPI" for d in range(40)}
    res = evaluate(cls(), bars, Context(news_days=days))
    for t in res.trades:
        d = datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO)
        hm = d.hour * 100 + d.minute
        assert 720 <= hm < 930 and not (700 <= hm < 800)
    rows = cls().checklist(bars, Context())
    assert rows[-1][0] == "Checks passed"


def test_es_breakout_signals_only_in_window_and_flat_by_3pm():
    cls = strategies.get("es_range_breakout")
    c, v = random_walk(seed=3)
    bars = make_bars(c, vol=v)
    res = evaluate(cls(), bars, Context())
    assert res.trades
    for t in res.trades:
        d = datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO)
        assert 930 <= d.hour * 100 + d.minute < 1100 and t.direction == 1
        if not t.is_open:
            x = bars[t.exit_index].dt
            assert x.date() == d.date() and (x.hour * 100 + x.minute) <= 1500


def test_example_config_loads():
    s = load(ROOT / "config.example.toml")
    assert [w.name for w in s.watches] == ["gold", "es"]
    g = s.watch("gold")
    assert g.contracts["/MGC"] == 10 and g.params["atr_mult"] == 1.75
    for w in s.watches:
        cls = strategies.get(w.strategy)
        cls.make_params(w.params)
