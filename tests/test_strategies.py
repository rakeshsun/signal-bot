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


def test_runner_takes_part_at_target_then_exits_on_choch():
    from signalbot.bars import Bar
    from signalbot.engine import Signal, simulate

    # long at 100: target 102 takes half off, price climbs, makes a swing low at 103, then closes below it
    path = [(100, 100), (100, 101), (101, 102.5), (102.5, 104), (104, 103), (103, 105), (105, 106), (106, 107), (107, 102)]
    bars = [Bar(time=i * 60_000, open=o, high=max(o, c) + 0.2, low=min(o, c) - 0.2, close=c, volume=1) for i, (o, c) in enumerate(path)]
    sigs = [None] * len(bars)
    sigs[0] = Signal(1, stop=98, target=102, runner=0.5)
    lows = [None] * len(bars)
    lows[6] = 102.8  # swing low printed at bar 4, confirmed 2 bars later
    res = simulate(bars, sigs, structure=([None] * len(bars), lows, 2))
    t = res.trades[0]
    assert t.part_price == 102 and t.part_index == 2
    assert t.exit_reason == "choch" and t.exit_index == 8 and t.exit_price == 102
    assert t.points == 0.5 * (102 - 100) + 0.5 * (102 - 100)
    assert [e.kind for e in res.events] == ["entry", "partial", "exit"]


def test_runner_stop_moves_to_entry_after_target():
    from signalbot.bars import Bar
    from signalbot.engine import Signal, simulate

    path = [(100, 100), (100, 101), (101, 102.5), (102.5, 101), (101, 99)]
    bars = [Bar(time=i * 60_000, open=o, high=max(o, c) + 0.2, low=min(o, c) - 0.2, close=c, volume=1) for i, (o, c) in enumerate(path)]
    sigs = [None] * len(bars)
    sigs[0] = Signal(1, stop=98, target=102, runner=0.5)
    t = simulate(bars, sigs, structure=([None] * 5, [None] * 5, 2)).trades[0]
    assert t.exit_reason == "stop" and t.exit_price == 100  # stopped at entry, not at 98
    assert t.points == 1.0  # half +2, half 0


def test_bos_trend_filter_only_trades_with_the_trend():
    from signalbot.strategies.base import Context as Ctx

    c, v = random_walk()
    B = strategies.get("bos_pullback")
    allday = {"session_start": 0, "session_end": 2400}
    removed = 0
    for series in (c, [2 * c[0] - x for x in c]):  # the walk and its mirror image
        bs = make_bars(series, vol=v)
        s = B(B.make_params({**allday, "trend_ema": 200}))
        ema = s._trend(bs)
        sigs = [(i, x) for i, x in enumerate(s.signals(bs, Ctx("X"))) if x]
        unfiltered = [x for x in B(B.make_params(allday)).signals(bs, Ctx("X")) if x]
        assert all((bs[i].close - ema[i]) * x.direction > 0 for i, x in sigs)
        removed += len(unfiltered) - len(sigs)
    assert removed > 0


def _pb_bars(closes: list[float], wick: float = 3.0):
    """Bars that trend with the closes and have a `wick` deep enough to reach a lagging EMA."""
    from signalbot.bars import Bar

    return [
        Bar(time=1_762_000_000_000 + i * 900_000, open=c, high=c + wick, low=c - wick, close=c, volume=1)
        for i, c in enumerate(closes)
    ]


def test_ema_pullback_needs_a_trend_and_a_close_back_above_the_fast_ema():
    from signalbot.strategies import get
    from signalbot.strategies.base import Context

    cls = get("ema_pullback")
    p = {"fast": 5, "slow": 10, "atr_len": 5, "session_start": 0, "session_end": 2400}
    strat = cls(cls.make_params(p))

    up = [100.0 + i for i in range(40)]
    assert any(s and s.direction == 1 for s in strat.signals(_pb_bars(up), Context()))

    # the mirror image must give shorts and never a long
    dn = [200.0 - i for i in range(40)]
    sigs = strat.signals(_pb_bars(dn), Context())
    assert any(s and s.direction == -1 for s in sigs)
    assert not any(s and s.direction == 1 for s in sigs)

    # flat market: price is never clear of the slow EMA, so there is no trend to pull back into
    assert not any(strat.signals(_pb_bars([100.0] * 40), Context()))


def test_ema_pullback_respects_do_long_and_do_short():
    from signalbot.strategies import get
    from signalbot.strategies.base import Context

    cls = get("ema_pullback")
    base = {"fast": 5, "slow": 10, "atr_len": 5, "session_start": 0, "session_end": 2400}
    up = _pb_bars([100.0 + i for i in range(40)])
    assert not any(cls(cls.make_params({**base, "do_long": False})).signals(up, Context()))
    assert any(cls(cls.make_params({**base, "do_short": False})).signals(up, Context()))


def test_ema_pullback_only_fires_inside_its_session():
    from signalbot.bars import hhmm
    from signalbot.strategies import get
    from signalbot.strategies.base import Context

    cls = get("ema_pullback")
    bars = _pb_bars([100.0 + i for i in range(400)])
    sigs = cls(cls.make_params({"fast": 5, "slow": 10, "atr_len": 5})).signals(bars, Context())
    fired = {hhmm(bars[i].time) for i, s in enumerate(sigs) if s}
    assert fired, "expected at least one signal"
    assert all(820 <= t < 1000 for t in fired), f"fired outside 8:20-10:00 CT: {sorted(fired)}"


def test_uptime_gap_only_reports_missed_market_time(tmp_path):
    from datetime import datetime

    from signalbot import uptime
    from signalbot.bars import CHICAGO
    from signalbot.runner import market_open

    def gap(a, b):
        uptime.touch(tmp_path, a.timestamp())
        return uptime.gap_since(tmp_path, market_open, 20, now=b.timestamp())

    # never run before: nothing to report
    assert uptime.gap_since(tmp_path, market_open) is None

    # Thursday evening to Friday morning: the market was open nearly all of it
    g = gap(datetime(2026, 11, 5, 18, 5, tzinfo=CHICAGO), datetime(2026, 11, 6, 7, 12, tzinfo=CHICAGO))
    assert g and "13h" in g[0]
    assert "6:05 PM" in g[1] and "7:12 AM" in g[1]

    # the whole weekend, with the market shut: nothing was missed
    assert gap(datetime(2026, 11, 7, 10, 0, tzinfo=CHICAGO), datetime(2026, 11, 8, 10, 0, tzinfo=CHICAGO)) is None

    # a quick restart is not an outage
    assert gap(datetime(2026, 11, 5, 2, 0, tzinfo=CHICAGO), datetime(2026, 11, 5, 2, 3, tzinfo=CHICAGO)) is None

    # 90 minutes of London session is
    g = gap(datetime(2026, 11, 5, 2, 0, tzinfo=CHICAGO), datetime(2026, 11, 5, 3, 30, tzinfo=CHICAGO))
    assert g and "1h 30m" in g[0]


def test_uptime_open_minutes_counts_only_trading_hours():
    from datetime import datetime

    from signalbot import uptime
    from signalbot.bars import CHICAGO
    from signalbot.runner import market_open

    # Friday 3 PM to Sunday 6 PM CT: trading stops at 4 PM Friday, resumes 5 PM Sunday
    mins = uptime.open_minutes(datetime(2026, 11, 6, 15, 0, tzinfo=CHICAGO),
                               datetime(2026, 11, 8, 18, 0, tzinfo=CHICAGO), market_open)
    assert 100 <= mins <= 130, mins  # ~1h Friday + ~1h Sunday


def test_uptime_ping_never_raises():
    from signalbot import uptime

    assert uptime.ping("") is False
    assert uptime.ping("http://127.0.0.1:9/nope", timeout=0.3) is False


def test_no_posix_only_date_formats_anywhere():
    """%-I and %-d are glibc extensions. On Windows they raise ValueError at render time, which
    would take the dashboard down rather than merely look wrong, so no source file may use them."""
    import re
    import tokenize
    from pathlib import Path

    import signalbot

    bad = []
    for path in sorted(Path(signalbot.__file__).parent.rglob("*.py")):
        with path.open("rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type != tokenize.STRING or '"""' in tok.string:
                    continue
                if re.search(r"%-[a-zA-Z]", tok.string):
                    bad.append(f"{path.name}:{tok.start[0]} {tok.string[:50]}")
    assert not bad, "POSIX-only strftime directives (use the field arithmetic instead): " + "; ".join(bad)


def test_clock_and_day_render_without_strftime_extensions():
    from datetime import datetime

    from signalbot.dashboard import _ampm, _mday

    assert _ampm(datetime(2026, 11, 8, 15, 18)) == "3:18 PM"
    assert _ampm(datetime(2026, 11, 8, 0, 5)) == "12:05 AM"   # midnight is 12, not 0
    assert _ampm(datetime(2026, 11, 8, 12, 0)) == "12:00 PM"  # noon is PM
    assert _ampm(datetime(2026, 11, 8, 9, 0)) == "9:00 AM"    # no leading zero
    assert _mday(datetime(2026, 11, 8)) == "Nov 8"
    assert _mday(datetime(2026, 11, 22)) == "Nov 22"
