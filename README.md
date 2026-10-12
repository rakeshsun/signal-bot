# signal-bot

A small framework for **trading strategy alerts**: live candles from your **tastytrade** account
come in, your strategies run on every closed bar, and you get a **phone notification** when one
of them wants to trade, plus another when its stop, target or end-of-day exit is hit.

Strategies are plug-ins. Each one is a single Python file containing only its entry rules; fills,
stops, targets, one-trade-at-a-time, backtesting, alerts and the live feed are shared.

It **only sends alerts**. It never places orders.

Included strategies:

| Strategy | Market | Rules | Status |
|---|---|---|---|
| `metals_confluence` | Gold futures (GC data → trade /MGC or /GC) | 15m, 8 confirming checks, 7:20–9:30 CT, 1.75 ATR stop, 1.5R target | main strategy (TradingView backtest PF ≈ 3) |
| `es_range_breakout` | S&P futures (ES → /MES or /ES) | 15m breakout of the day's high, 9:30–11:00 CT, 1.25 ATR stop, 1R, flat by 3pm | thin edge, off by default |

Example alert:
```
BUY /GCZ6 — gold 8/8
Signal on the bar that closed 08:30 AM CT, close 4,213.6
FROM YOUR FILL: stop 14.00 pts below, target 21.00 pts above
(levels from signal close: SL 4,199.6 · TP 4,234.6)
/MGC: risk $140 · reward $210
/GC: risk $1,400 · reward $2,100
```

## Layout

```
src/signalbot/
  bars.py            Bar + time helpers
  indicators.py      EMA, RMA, SMA, RSI, ATR, DMI/ADX, MACD, barssince - same formulas as Pine Script
  engine.py          trade simulation (next-open fills, stop/target, TradingView same-bar rule, time exit)
  strategies/
    base.py          Strategy base class (what a plug-in implements)
    metals_confluence.py
    es_range_breakout.py
    _template.py     copy this to start a new strategy
  data/tastytrade.py live + historical candles (DXLink), futures or stocks
  data/csvfile.py    bars from CSV (offline backtests)
  runner.py          live loop: one stream per watch, alerts, heartbeat, stale-data watchdog
  news.py notify.py messages.py config.py cli.py
tests/               unit tests + TradingView trade list for parity checks
```

## Setup (Mac, about 15 minutes)

### 1. Install
```bash
git clone https://github.com/<you>/signal-bot.git && cd signal-bot
./scripts/setup_mac.sh        # venv, install, config.toml + .env from the examples, tests
source .venv/bin/activate
```
Python 3.11+ (`brew install python@3.12` if `python3 --version` is older).

### 2. tastytrade API access (one time)
1. tastytrade web → **Manage → My Profile → API** → opt in / open **OAuth Applications**.
2. Create an application: read + market-data scopes (no trading scope needed), callback
   `http://localhost:8000`. Save the **client secret**.
3. Open it → **Manage → Create Grant** → save the **refresh token** (doesn't expire).
4. In `.env`:
   ```
   TT_SECRET=...
   TT_REFRESH=...
   ```
Real-time futures quotes must be enabled on the account (tastytrade asks you to accept the CME
data agreement the first time you open a futures quote).

### 3. Phone notifications (ntfy, free)
1. Install **ntfy** on your phone. The developer is *Philipp Heckel*; not "Ntfy me".
2. Subscribe to a hard-to-guess topic, e.g. `rakesh-signals-k39xq`.
3. `config.toml` → `[notify] ntfy_topic = "rakesh-signals-k39xq"`.
4. `signalbot test-notify`. The message should appear on your phone.

### 4. Check, verify, run
```bash
signalbot check                 # each enabled watch: contract, last bar, checklist
signalbot backtest gold --days 110 --compare tests/fixtures/tradingview_gcz2026_trades.csv
signalbot run --dry-run         # prints alerts instead of sending
signalbot run                   # live
```
The `--compare` file is TradingView's own trade list for the gold strategy on GCZ2026
(Jul 8 – Oct 12, 2026, 29 trades). The backtest prints how many of those the bot also took, and
any missing or extra trades. Expect nearly all. Small differences can come from where history
starts (warm-up, whether a trade was already open) and data-vendor volume differences.

## Config: watches

Each `[[watch]]` runs one strategy on one instrument:

```toml
[[watch]]
name = "gold"                 # used in alerts and CLI (signalbot backtest gold)
strategy = "metals_confluence"
symbol = "auto"               # "auto" + product = active futures month; "/GCZ6"; or a ticker "SPY"
product = "GC"
interval = "15m"
enabled = true
news_alert = true             # 7:00 CT heads-up on CPI / jobs days
contracts = { "/MGC" = 10, "/GC" = 100 }   # $ per point lines in alerts
point_value = 100             # $ per point of the data symbol (backtest totals)

[watch.params]                # override any strategy param; omit = tested defaults
rr = 1.5
```
`signalbot strategies` lists strategies and their params.

## Adding a strategy

1. `cp src/signalbot/strategies/_template.py src/signalbot/strategies/my_idea.py`
2. Set `name`, `description`, the `Params` dataclass, and the rules in `signals()`. It returns
   one item per bar: `None`, or `Signal(direction, stop, target)`. Bar *i* may only look at
   bars up to *i*.
3. Optional: `checklist()` rows for status messages; `flat_hhmm` for intraday strategies.
4. Add a `[[watch]]` with `strategy = "my_idea"`, then run `signalbot backtest <watch> --days 120`.
5. Add a test in `tests/test_strategies.py`.

The engine handles next-bar-open fills, stop and target (including TradingView's rule for bars
that touch both), one trade at a time, and time exits. So a strategy written from a TradingView
script will give the same trades as the TradingView Strategy Tester.

## Run in the background (macOS)

Install once. After that the bot runs by itself; nothing to start each day.

```bash
./scripts/service.sh install
```

What it does (macOS `launchd`, file `~/Library/LaunchAgents/com.signalbot.plist`):
- starts now and **at every login**; no terminal or VS Code needs to stay open;
- **restarts itself** if it crashes or the connection drops;
- keeps the Mac from idle-sleeping while it runs (`caffeinate -i`);
- writes its log to `logs/signalbot.log`, **cleaned up automatically**: at midnight the day's
  log is renamed (`signalbot.log.2026-10-12`) and files older than 7 days are deleted.
  Crashes / startup errors go to `logs/crash.log`.

| Command | What it does |
|---|---|
| `./scripts/service.sh status` | is it running? |
| `./scripts/service.sh logs` | follow the live log (Ctrl+C to exit) |
| `./scripts/service.sh errors` | last startup errors / crashes |
| `./scripts/service.sh dashboard` | open the status page in your browser |
| `./scripts/service.sh restart` | after editing `config.toml` or pulling new code |
| `./scripts/service.sh stop` / `start` | stop / start it |
| `./scripts/service.sh uninstall` | remove the background service |

### Dashboard

At the top, one big word per watch tells you what to do now:

| Shows | When | What to do |
|---|---|---|
| **BUY** / **SELL** (flashing, tab title blinks) | a new signal, for 30 minutes after it fires | enter on Robinhood, then set the stop and limit shown |
| **HOLD** | a trade is open | nothing; keep your stop and limit in place |
| **EXIT** | a trade closed in the last 30 minutes | check Robinhood; cancel the leftover order (or close, for a time exit) |
| **NO ACTION** | flat | nothing; it says whether it's watching now or when the next window opens |

Below that, while the bot runs it rewrites `logs/dashboard.html` every minute: one card per watch with the
live 8-check table, the open trade (entry, stop, target, current P/L per contract), the recent
trades with win rate, the last 25 alerts, and upcoming news days. Open it with
`./scripts/service.sh dashboard` (or double-click the file); it refreshes itself every minute.
If its "updated" time is old, the bot has stopped.

### Day to day

- **Nothing to run daily.** Each weekday at 7:15 CT you get a "Signal bot: running" message with
  today's checklist; signals arrive when they fire (gold window 7:20–9:30 CT).
- **No 7:15 message?** Something is wrong: run `./scripts/service.sh status` and
  `./scripts/service.sh logs`. The bot also alerts you if candles stop arriving for 45 minutes.
- **Alerts are never lost or repeated.** One that fails to send (Wi-Fi blip) is retried the
  next minute; a restart right after a signal still delivers it; exits are always reported, even
  after a data gap. If the data stream goes quiet while the market is open, the bot reconnects
  by itself, and only messages you if that doesn't help. Futures contract rolls are followed
  automatically. If the bot can't start (e.g. expired tastytrade token) you get one message a day.
- **Mac must be awake.** With the lid closed on battery it sleeps and no alerts go out. Keep
  it plugged in, or open, during your trading window.
- **Changed `config.toml` or updated the code** (`git pull`): `./scripts/service.sh restart`.
- **Logs clean themselves up** (new file each midnight, 7 days kept). To keep more or fewer
  days, change `--keep-days` in `scripts/service.sh` and run `./scripts/service.sh restart`.
- **Duplicate alerts?** Don't also run `signalbot run` in a terminal while the service is
  running. Use `signalbot run --dry-run` for experiments, or stop the service first.

## Other details

- **Contract rolls**: `symbol = "auto"` follows tastytrade's active month automatically.
- **News days**: `[news_days]` in config (2026 CPI / jobs dates) plus this week's ForexFactory
  calendar when `fetch_news_calendar = true`. Add 2027 dates when BLS publishes them.
- **Heartbeat**: weekdays at 7:15 CT, a "running" message with each watch's checklist.
- **Watchdog**: an alert if a watch gets no new candles for 45 minutes while the market is open.

## Development
```bash
pytest
```
`tests/fixtures/metals_regression.json` locks the gold strategy's trades so refactors can't
silently change them.

---
Educational tool, not financial advice. Backtests don't guarantee future results; paper trade first.
