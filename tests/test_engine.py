from signalbot.bars import Bar
from signalbot.engine import Signal, Trade, first_touch, simulate

from helpers import make_bars


def test_first_touch_rules():
    b = Bar(0, 100, 105, 95, 101, 1)
    assert first_touch(b, stop=96, target=104, direction=1) == ("target", 104)  # high nearer/equal -> high first
    assert first_touch(Bar(0, 100, 108, 98, 101, 1), 99, 107, 1) == ("stop", 99)  # low nearer -> low first
    assert first_touch(Bar(0, 90, 92, 89, 91, 1), 95, 110, 1) == ("stop", 90)  # gap through stop fills at open
    assert first_touch(Bar(0, 100, 101, 99, 100, 1), 95, 110, 1) is None


def test_fill_next_open_and_one_trade_at_a_time():
    bars = make_bars([100, 101, 102, 103, 104, 105, 106])
    sigs = [None, Signal(1, 90, 104.4), Signal(1, 90, 200), None, Signal(1, 90, 200), None, None]
    res = simulate(bars, sigs)
    t = res.trades[0]
    assert t.entry_index == 2 and t.entry_price == bars[2].open
    assert t.exit_reason == "target" and t.exit_index == 4  # bar 4 is the first high above 104.4
    # signal on bar 2 ignored (trade active); bar 4 closes the trade after the fill bar,
    # so its own signal is taken (same as the Pine script)
    assert len(res.trades) == 2 and res.trades[1].signal_index == 4


def test_time_exit_at_next_open():
    from datetime import datetime
    from signalbot.bars import CHICAGO
    bars = make_bars([100] * 12, start=datetime(2026, 10, 5, 14, 0, tzinfo=CHICAGO))
    sigs = [Signal(1, 50, 200)] + [None] * 11
    res = simulate(bars, sigs, flat_hhmm=1500)
    t = res.trades[0]
    assert t.exit_reason == "time" and t.exit_price == bars[t.exit_index].close
    assert bars[t.exit_index].dt.hour == 15 and bars[t.exit_index].dt.minute == 0


def test_trade_points():
    t = Trade(direction=-1, signal_index=0, signal_time=0, signal_close=100, stop=110, target=85)
    t.entry_price, t.exit_price = 100, 85
    assert t.points == 15 and t.won and t.risk == 10 and t.reward == 15
