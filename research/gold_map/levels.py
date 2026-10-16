"""Gold daily market map (usage: python research/gold_map/levels.py data/GC_1m.csv levels.csv)

Price/volume levels; known before the US session (7:00 CT).

Levels per CME day (17:00 CT -> 16:00 CT next day):
  PDH/PDL/PDC  prior CME day high / low / close        PWH/PWL  prior week high / low
  ASH/ASL      Asia 17:00-02:00                        LDH/LDL  London 02:00-07:00
  ONH/ONL      overnight 17:00-07:00                    POC/VAH/VAL prior day volume profile (70% value area)
  PIV          classic pivot (H+L+C)/3 of prior day     LIS = prior-day POC (line in the sand)
  EM±1/±2      expected-move zones: 7:00 price ± 1/2 x sigma, sigma = std of the last 20 daily moves
  ATR14        daily ATR; 'used' = today's range / ATR
"""
import numpy as np, pandas as pd

def load_1m(path):
    d = pd.read_csv(path)
    d.index = pd.DatetimeIndex(pd.to_datetime(d.time, unit="s", utc=True)).tz_convert("America/Chicago")
    return d.drop(columns="time")

def cme_day(idx):
    """The CME trading date a bar belongs to (bars from 17:00 count for the next day)."""
    shifted = idx + pd.Timedelta(hours=7)  # 17:00 -> 00:00 next day
    return shifted.normalize().tz_localize(None)

def volume_profile(df, bin_=0.5, va=0.70):
    px = (np.round(((df.high + df.low + df.close) / 3) / bin_) * bin_)
    vol = df.volume.groupby(px).sum().sort_index()
    if vol.empty:
        return np.nan, np.nan, np.nan
    poc = vol.idxmax(); total = vol.sum()
    prices = list(vol.index); i = prices.index(poc); lo = hi = i; acc = vol.iloc[i]
    while acc < va * total and (lo > 0 or hi < len(prices) - 1):
        up = vol.iloc[hi + 1] if hi < len(prices) - 1 else -1
        dn = vol.iloc[lo - 1] if lo > 0 else -1
        if up >= dn: hi += 1; acc += up
        else: lo -= 1; acc += dn
    return poc, prices[hi], prices[lo]

def build_map(m1):
    m1 = m1.copy(); m1["day"] = cme_day(m1.index); hm = m1.index.hour * 100 + m1.index.minute
    m1["hm"] = hm
    by = dict(tuple(m1.groupby("day")))
    days = sorted(m1.day.unique())
    daily = m1.groupby("day").agg(high=("high", "max"), low=("low", "min"), close=("close", "last"), open=("open", "first"))
    tr = pd.concat([daily.high - daily.low, (daily.high - daily.close.shift()).abs(), (daily.low - daily.close.shift()).abs()], axis=1).max(axis=1)
    daily["atr14"] = tr.rolling(14).mean()
    daily["sig20"] = daily.close.diff().rolling(20).std()
    wk = daily.groupby(pd.Index(daily.index).to_period("W-FRI")).agg(wh=("high", "max"), wl=("low", "min"))
    rows = []
    for k in range(1, len(days)):
        d, p = days[k], days[k - 1]
        cur, prev = by[d], by[p]
        pre = cur[(cur.hm >= 1700) | (cur.hm < 700)]
        if len(prev) < 300 or len(pre) < 200 or np.isnan(daily.atr14.get(p, np.nan)):
            continue
        asia = pre[(pre.hm >= 1700) | (pre.hm < 200)]; lon = pre[(pre.hm >= 200) & (pre.hm < 700)]
        poc, vah, val = volume_profile(prev)
        pw = pd.Period(d, "W-FRI") - 1
        anchor = pre.close.iloc[-1]
        sig = daily.sig20.get(p)
        ph, pl, pc = daily.loc[p, ["high", "low", "close"]]
        rows.append(dict(day=d, anchor=anchor, PDH=ph, PDL=pl, PDC=pc, PIV=(ph + pl + pc) / 3,
                         PWH=wk.wh.get(pw, np.nan), PWL=wk.wl.get(pw, np.nan),
                         ASH=asia.high.max(), ASL=asia.low.min(), LDH=lon.high.max(), LDL=lon.low.min(),
                         ONH=pre.high.max(), ONL=pre.low.min(), POC=poc, VAH=vah, VAL=val,
                         EMu1=anchor + sig, EMd1=anchor - sig, EMu2=anchor + 2 * sig, EMd2=anchor - 2 * sig,
                         ATR=daily.atr14.get(p), SIG=sig))
    return pd.DataFrame(rows).set_index("day")

if __name__ == "__main__":
    import sys
    m = build_map(load_1m(sys.argv[1]))
    m.to_csv(sys.argv[2]); print(m.tail(3).T)
