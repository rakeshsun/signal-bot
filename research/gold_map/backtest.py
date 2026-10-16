"""Level Beast style setups on gold 5-minute bars, one trade per setup per day, costs 0.35 pts.

usage: python research/gold_map/backtest.py data/GC_1m.csv levels.csv
Setups: LIS (prior-day POC cross), ZONE_FADE (+/-1 sigma poke and close back inside),
LEVEL_REJECT (wick through a map level, close back), LEVEL_BREAK (close through a level).
"""
import sys, os, itertools; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from levels import load_1m, cme_day
DATA, LEVELS_CSV = sys.argv[1], sys.argv[2]; COST = 0.35; PPT = 10; SPLIT = pd.Timestamp('2026-06-01')
m = load_1m(DATA)
b5 = m.resample('5min', label='left', closed='left').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
b5['day'] = cme_day(b5.index); b5['hm'] = b5.index.hour*100 + b5.index.minute
mp = pd.read_csv(LEVELS_CSV, index_col=0, parse_dates=True)
by = dict(tuple(b5.groupby('day')))

def trade(bars, i, d, stop, tgt, flat=1500):
    """enter next bar open; stop first if both hit in one bar; flat at `flat`."""
    if i + 1 >= len(bars): return None
    e = bars.open.iloc[i+1]
    if (e - stop) * d <= 0 or (tgt - e) * d <= 0: return None
    for j in range(i+1, len(bars)):
        h, l, o = bars.high.iloc[j], bars.low.iloc[j], bars.open.iloc[j]
        if (o - stop) * d <= 0: return (o - e) * d
        if (l <= stop <= h): return (stop - e) * d
        if (l <= tgt <= h): return (tgt - e) * d
        if bars.hm.iloc[j] >= flat: return (bars.close.iloc[j] - e) * d
    return (bars.close.iloc[-1] - e) * d

def run(setup, rr, window=(720, 1100), buf=0.1, levels=('PDH','PDL','ONH','ONL','VAH','VAL','ASH','ASL','LDH','LDL','PWH','PWL')):
    out = []
    for day, row in mp.iterrows():
        if day not in by: continue
        bars = by[day]; bars = bars[(bars.hm >= 700) & (bars.hm < 1600)].reset_index(drop=True)
        if len(bars) < 90: continue
        A = row.ATR; B = buf * A
        for i in range(1, len(bars)):
            t = bars.hm.iloc[i]
            if not (window[0] <= t < window[1]): continue
            o, h, l, c, pc = bars.open.iloc[i], bars.high.iloc[i], bars.low.iloc[i], bars.close.iloc[i], bars.close.iloc[i-1]
            sig = None
            if setup == 'LIS':  # close crosses the prior-day POC: trade the new side
                L = row.POC
                if pc <= L < c: sig = (1, L - B)
                elif pc >= L > c: sig = (-1, L + B)
            elif setup == 'ZONE_FADE':  # pokes outside +/-1 sigma, closes back inside
                if h > row.EMu1 and c < row.EMu1 and c < o: sig = (-1, h + B)
                elif l < row.EMd1 and c > row.EMd1 and c > o: sig = (1, l - B)
            elif setup == 'ZONE_FADE_LOW':  # only the -1 sigma bounce (the one level that held)
                if l < row.EMd1 and c > row.EMd1 and c > o: sig = (1, l - B)
            elif setup == 'LEVEL_REJECT':  # wick through a map level, close back on the old side
                for n in levels:
                    L = row[n]
                    if not np.isfinite(L): continue
                    if h > L and c < L and pc < L and c < o: sig = (-1, h + B); break
                    if l < L and c > L and pc > L and c > o: sig = (1, l - B); break
            elif setup == 'LEVEL_BREAK':  # close through a map level from the other side
                for n in levels:
                    L = row[n]
                    if not np.isfinite(L): continue
                    if pc <= L < c and c > o: sig = (1, L - B); break
                    if pc >= L > c and c < o: sig = (-1, L + B); break
            if sig:
                d, stop = sig; e = bars.open.iloc[i+1] if i+1 < len(bars) else c
                risk = (e - stop) * d
                if risk <= 0.5: continue
                r = trade(bars, i, d, stop, e + d * rr * risk)
                if r is not None: out.append((day, r - COST)); break  # one trade per day per setup
    return out

def rep(name, tr):
    if len(tr) < 15: print(f'{name:<38} n={len(tr)} (too few)'); return
    p = np.array([t[1] for t in tr]) * PPT
    def pf(x): g=x[x>0].sum(); l=-x[x<=0].sum(); return g/l if l else 9
    h1 = np.array([t[1] for t in tr if t[0] < SPLIT])*PPT; h2 = np.array([t[1] for t in tr if t[0] >= SPLIT])*PPT
    eq = np.cumsum(p); dd = (np.maximum.accumulate(eq) - eq).max()
    print(f'{name:<38} n={len(p):>3} won={np.mean(p>0):4.0%} PF={pf(p):4.2f} net=${p.sum():>7,.0f} DD=${dd:>6,.0f} PF1/2={pf(h1):.2f}/{pf(h2):.2f}')

for setup in ('LIS','ZONE_FADE','ZONE_FADE_LOW','LEVEL_REJECT','LEVEL_BREAK'):
    print(f'-- {setup}')
    for rr, win in itertools.product((1.0, 1.5, 2.0), ((720, 1100), (830, 1300))):
        rep(f'  rr {rr} window {win[0]}-{win[1]}', run(setup, rr, win))
