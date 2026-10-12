import random
from datetime import datetime, timedelta

from goldbot.strategy import CHICAGO, Bar, Params, Trade, _first_touch, run

BAR = timedelta(minutes=15)


def make_bars(closes, start=datetime(2026, 10, 5, 0, 0, tzinfo=CHICAGO), vol=None):
    bars = []
    t = start
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append(Bar(int(t.timestamp() * 1000), o, max(o, c) + 0.5, min(o, c) - 0.5, c, (vol or [100] * len(closes))[i]))
        prev = c
        t += BAR
    return bars


def test_first_touch_rules():
    b = Bar(0, 100, 105, 95, 101, 1)
    # long: both touched, open 100: high 5 away, low 5 away -> high first (<=) -> target first
    assert _first_touch(b, stop=96, target=104, direction=1) == ("target", 104)
    b2 = Bar(0, 100, 108, 98, 101, 1)  # low nearer -> low first -> stop for a long
    assert _first_touch(b2, stop=99, target=107, direction=1) == ("stop", 99)
    gap = Bar(0, 90, 92, 89, 91, 1)
    assert _first_touch(gap, stop=95, target=110, direction=1) == ("stop", 90)
    assert _first_touch(Bar(0, 100, 101, 99, 100, 1), 95, 110, 1) is None


def _random_walk(n=3000, seed=7):
    rnd = random.Random(seed)
    closes, x = [], 2000.0
    for _ in range(n):
        x += rnd.gauss(0.15, 2.0)  # drifting market so the checklist lines up now and then
        closes.append(round(x, 1))
    vols = [rnd.randint(50, 200) for _ in closes]
    return closes, vols


def test_run_produces_only_session_entries_and_one_trade_at_a_time():
    closes, vols = _random_walk()
    bars = make_bars(closes, vol=vols)
    res = run(bars, Params())
    assert res.trades, "expected some signals"
    for t in res.trades:
        hhmm = datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO)
        assert 720 <= hhmm.hour * 100 + hhmm.minute < 930
        assert t.entry_index == t.signal_index + 1
        if not t.is_open:
            assert t.exit_reason in ("stop", "target")
    # trades never overlap: each new signal comes after the previous trade closed
    for a, b in zip(res.trades, res.trades[1:]):
        assert a.exit_index is not None and b.signal_index >= a.exit_index


def test_news_day_blocks_7_to_8_and_sends_heads_up():
    closes, vols = _random_walk()
    bars = make_bars(closes, vol=vols)
    days = {bars[0].dt.date() + timedelta(days=d): "CPI" for d in range(0, 40)}
    res = run(bars, Params(), news_days=days)
    for t in res.trades:
        d = datetime.fromtimestamp(t.signal_time / 1000, tz=CHICAGO)
        if d.date() in days:
            assert not (700 <= d.hour * 100 + d.minute < 800)
    assert any(e.kind == "news" for e in res.events)


def test_trade_levels():
    t = Trade(direction=-1, signal_index=0, signal_time=0, signal_close=100, risk=10, stop=110, target=85)
    t.entry_price, t.exit_price = 100, 85
    assert t.points == 15 and t.won
