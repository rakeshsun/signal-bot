import random
from datetime import datetime, timedelta

from signalbot.bars import CHICAGO, Bar

BAR = timedelta(minutes=15)


def make_bars(closes, start=datetime(2026, 10, 5, 0, 0, tzinfo=CHICAGO), vol=None):
    bars, t, prev = [], start, closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append(Bar(int(t.timestamp() * 1000), o, max(o, c) + 0.5, min(o, c) - 0.5, c, (vol or [100] * len(closes))[i]))
        prev = c
        t += BAR
    return bars


def random_walk(n=3000, seed=7):
    rnd = random.Random(seed)
    closes, x = [], 2000.0
    for _ in range(n):
        x += rnd.gauss(0.15, 2.0)
        closes.append(round(x, 1))
    vols = [rnd.randint(50, 200) for _ in closes]
    return closes, vols
