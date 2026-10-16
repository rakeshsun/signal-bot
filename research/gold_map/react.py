"""Do gold's market-map levels hold better than random prices?

usage: python research/gold_map/react.py data/GC_1m.csv levels.csv
(levels.csv from: python research/gold_map/levels.py data/GC_1m.csv levels.csv)
For each level, the first touch in the 7:00-16:00 CT session: did price bounce k x ATR away
before breaking through by the same amount? Compared with 30 random levels per day.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from levels import load_1m, cme_day
DATA, LEVELS_CSV = sys.argv[1], sys.argv[2]
m = load_1m(DATA); m['day'] = cme_day(m.index); m['hm'] = m.index.hour*100 + m.index.minute
mp = pd.read_csv(LEVELS_CSV, index_col=0, parse_dates=True)
by = dict(tuple(m.groupby('day')))
LEVELS = ['PDH','PDL','PDC','PIV','PWH','PWL','ASH','ASL','LDH','LDL','ONH','ONL','POC','VAH','VAL','EMu1','EMd1','EMu2','EMd2']
rng = np.random.default_rng(7)

def react(day_bars, L, side, b):
    """side +1: price is above L, expect support (bounce up); -1: resistance. Returns 1 hold, 0 break, None untouched."""
    H, Lo = day_bars.high.values, day_bars.low.values
    touch = np.where((Lo <= L) & (H >= L))[0]
    if not len(touch): return None
    i = touch[0]
    for j in range(i, len(H)):
        up_hit = H[j] >= L + b; dn_hit = Lo[j] <= L - b
        good, bad = (up_hit, dn_hit) if side > 0 else (dn_hit, up_hit)
        if bad: return 0          # break (also when both in one minute: conservative)
        if good: return 1
    return None

res = {k: {} for k in (0.05, 0.1, 0.2)}
for d, row in mp.iterrows():
    if d not in by: continue
    bars = by[d]; us = bars[(bars.hm >= 700) & (bars.hm < 1600)]
    if len(us) < 400: continue
    start = row.anchor
    for k in res:
        b = k * row.ATR
        for name in LEVELS:
            L = row[name]
            if not np.isfinite(L) or abs(L - start) < b: continue  # already at the level at 7:00
            r = react(us, L, 1 if start > L else -1, b)
            if r is not None: res[k].setdefault(name, []).append(r)
        for _ in range(30):  # random control levels within the usual day range
            L = start + rng.uniform(-1.5, 1.5) * row.SIG
            if abs(L - start) < b: continue
            r = react(us, L, 1 if start > L else -1, b)
            if r is not None: res[k].setdefault('RANDOM', []).append(r)

for k, d in res.items():
    base = np.mean(d['RANDOM']); nb = len(d['RANDOM'])
    print(f"\n== bounce of {k:.2f} x ATR (~{k*80:.0f} pts) before breaking through by the same: random levels hold {base:.1%} (n={nb})")
    for name in LEVELS:
        v = d.get(name, [])
        if len(v) < 20: continue
        p = np.mean(v); se = np.sqrt(base*(1-base)/len(v)); z = (p-base)/se
        print(f"  {name:<5} n={len(v):>3} hold={p:5.1%}  vs random {p-base:+6.1%}  z={z:+.1f}{'  *' if abs(z)>2 else ''}")

# Variance zones: does the US session stay inside?
rows=[]
for d,row in mp.iterrows():
    if d not in by: continue
    bars=by[d]; us=bars[(bars.hm>=700)&(bars.hm<1600)]
    if len(us)<400: continue
    hi,lo,cl=us.high.max(),us.low.min(),us.close.iloc[-1]
    rows.append(dict(in1=(hi<=row.EMu1)&(lo>=row.EMd1), in2=(hi<=row.EMu2)&(lo>=row.EMd2),
                     close1=row.EMd1<=cl<=row.EMu1, close2=row.EMd2<=cl<=row.EMu2,
                     pdhl=(hi<=row.PDH)&(lo>=row.PDL), atr_used=(bars.high.max()-bars.low.min())/row.ATR))
v=pd.DataFrame(rows)
print(f"\n== variance zones, {len(v)} days (7:00-16:00 CT)")
print(f"  whole session stays inside ±1 sigma: {v.in1.mean():.0%}   inside ±2 sigma: {v.in2.mean():.0%}")
print(f"  session CLOSES inside ±1 sigma: {v.close1.mean():.0%}   inside ±2 sigma: {v.close2.mean():.0%}")
print(f"  stays inside prior day high/low: {v.pdhl.mean():.0%}   median full-day range / ATR: {v.atr_used.median():.2f}")
