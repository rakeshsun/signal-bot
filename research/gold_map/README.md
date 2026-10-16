# Gold market map (Level Beast style) — research

Level Beast Pro publishes daily levels for SPX/ES/NQ/QQQ (not gold). Its options/gamma levels
(Regime Line, Magnets, Pin Walls) can't be rebuilt without options data. These scripts rebuild the
price/volume parts for gold and test them on one year of GC 1-minute data (Oct 2025 - Oct 2026).
Needs pandas + numpy (`pip install pandas numpy`).

    python research/gold_map/levels.py   data/GC_1m.csv levels.csv   # daily map
    python research/gold_map/react.py    data/GC_1m.csv levels.csv   # do levels hold vs random?
    python research/gold_map/backtest.py data/GC_1m.csv levels.csv   # trade setups

Levels (known at 7:00 CT): prior day/week high-low-close, pivot, Asia / London / overnight
high-low, prior-day volume profile POC (= LIS) / VAH / VAL, expected-move zones +/-1 and 2 sigma
(sigma = std of the last 20 daily moves), daily ATR.

## Results (Oct 16 2026)

Do levels hold better than random prices? (first touch 7:00-16:00, bounce vs break by k x ATR)
- No level type beat random consistently. Random levels held 53-59%. Most map levels were
  within noise; overnight high and value-area high *broke* more often than random.
- Only the -1 sigma expected-move zone held ~70% at every size tested (z up to 2.1). With 17
  level types x 3 sizes, one such result is also what chance alone would produce.
- "Holds ~85%": the session CLOSE is inside +/-1 sigma on 86% of days, but price trades outside
  it during the session on 31% of days.

Trade setups, 5-minute bars, 1 MGC, costs included (best window per setup):
- LIS (POC cross), 8:30-13:00, 1R target: 70 trades, 60% won, PF 1.54, +$2.4k/yr. The 7:20-11:00
  window loses, so this is weak evidence.
- Variance-zone fade, level rejection fade, level breakout: all lose money (PF 0.55-0.96).

Conclusion: useful as a picture of where price has been, not as a trading edge for gold.
