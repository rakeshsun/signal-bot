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


def test_metals_overnight_window_and_skip_bars():
    cls = strategies.get("metals_confluence")
    c, v = random_walk(seed=5)
    bars = make_bars(c, vol=v)
    asia = evaluate(cls(cls.make_params({"session_start": 1800, "session_end": 200})), bars, Context())
    assert asia.trades
    for t in asia.trades:
        d = datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO)
        hm = d.hour * 100 + d.minute
        assert hm >= 1800 or hm < 200
    base = evaluate(cls(), bars, Context())
    times = {datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO).strftime("%H%M") for t in base.trades}
    skip = sorted(times)[0]
    skipped = evaluate(cls(cls.make_params({"skip_bars": str(int(skip))})), bars, Context())
    assert all(datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO).strftime("%H%M") != skip for t in skipped.trades)


def test_levels_fvg_runs_all_modes_without_lookahead():
    cls = strategies.get("levels_fvg")
    c, v = random_walk(seed=11)
    bars = make_bars(c, vol=v)
    for mode in ("level_fvg", "sweep_fvg", "fvg_retest", "level_reject"):
        p = cls.make_params({"mode": mode, "session_start": 0, "session_end": 2359, "trend_filter": False})
        full = cls(p).signals(bars, Context())
        cut = cls(p).signals(bars[:2000], Context())
        # signals on the first 2000 bars must not change when later bars are added
        assert [(s.direction, s.stop) if s else None for s in full[:2000]] == [(s.direction, s.stop) if s else None for s in cut]
        for s, b in zip(full, bars):
            if s:
                assert (b.close - s.stop) * s.direction > 0 and (s.target - b.close) * s.direction > 0


def test_smc_no_lookahead_and_sane_stops():
    cls = strategies.get("smc")
    c, v = random_walk(seed=23)
    bars = make_bars(c, vol=v)
    for zone in ("ob", "fvg"):
        p = cls.make_params({"zone": zone, "discount": False, "session_start": 0, "session_end": 2359})
        full = cls(p).signals(bars, Context())
        cut = cls(p).signals(bars[:2000], Context())
        assert [(s.direction, s.stop) if s else None for s in full[:2000]] == [(s.direction, s.stop) if s else None for s in cut]
        assert any(full)
        for s, b in zip(full, bars):
            if s:
                assert (b.close - s.stop) * s.direction > 0 and (s.target - b.close) * s.direction > 0


def test_vwap_fade_no_lookahead_and_direction():
    cls = strategies.get("vwap_fade")
    c, v = random_walk(seed=7)
    bars = make_bars(c, vol=v)
    p = cls.make_params({"stretch_atr": 1.5, "session_start": 0, "session_end": 2359})
    full = cls(p).signals(bars, Context())
    cut = cls(p).signals(bars[:2000], Context())
    assert [(s.direction, s.stop) if s else None for s in full[:2000]] == [(s.direction, s.stop) if s else None for s in cut]
    assert any(full)
    for s, b in zip(full, bars):
        if s:  # fades: short only on a red bar, long only on a green bar
            assert (b.close < b.open) if s.direction < 0 else (b.close > b.open)
            assert (b.close - s.stop) * s.direction > 0 and (s.target - b.close) * s.direction > 0


def test_bos_pullback_no_lookahead_and_sane_levels():
    cls = strategies.get("bos_pullback")
    c, v = random_walk(seed=11)
    bars = make_bars(c, vol=v)
    p = cls.make_params({"session_start": 0, "session_end": 2359})
    full = cls(p).signals(bars, Context())
    cut = cls(p).signals(bars[:2000], Context())
    assert [(s.direction, s.stop) if s else None for s in full[:2000]] == [(s.direction, s.stop) if s else None for s in cut]
    assert any(full)
    for s, b in zip(full, bars):
        if s:
            assert (b.close - s.stop) * s.direction > 0 and (s.target - b.close) * s.direction > 0
    assert cls(p).checklist(bars, Context())


def test_bos_pullback_stop_atr_widens_the_stop():
    from signalbot.strategies.base import Context as Ctx

    c, v = random_walk()
    bars = make_bars(c, vol=v)
    B = strategies.get("bos_pullback")
    allday = {"session_start": 0, "session_end": 2400}
    tight = [s for s in B(B.make_params(allday)).signals(bars, Ctx("X")) if s]
    wide = [s for s in B(B.make_params({**allday, "stop_atr": 1.5})).signals(bars, Ctx("X")) if s]
    assert tight and len(tight) == len(wide)
    assert all(abs(w.stop - w_t.stop) > 0 and (w.stop - w_t.stop) * w.direction < 0 for w, w_t in zip(wide, tight))
