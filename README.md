# gold-signal-bot

Live phone alerts for the **Metals Confluence** gold futures strategy, using free real-time
market data from your **tastytrade** account instead of a paid TradingView plan.

Every 15 minutes the bot takes the latest closed gold futures candle (/GC, the same contract
the TradingView backtest used), runs the same 8 checks as the TradingView script, and sends a push
notification when all 8 line up during 7:20–9:30 am CT. It also tells you when the trade's stop or
target is hit, warns you on CPI / jobs-report mornings, and pings you each weekday at 7:15 CT so
you know it is running.

It **only sends alerts**. It never places orders; you enter the trade yourself (bracket order on
/MGC or /GC at Robinhood or tastytrade).

```
BUY /GCZ6 — gold signal 8/8
Signal on the bar that closed 08:30 AM CT, close 4,213.6
FROM YOUR FILL: stop 14.0 pts below, target 21.0 pts above
(levels from signal close: SL 4,199.6 · TP 4,234.6)
/MGC: risk $140 · reward $210
/GC:  risk $1,400 · reward $2,100
```

## How it matches TradingView

| Rule | TradingView script | Bot |
|---|---|---|
| Data | GCZ2026, 15 min, all hours | tastytrade /GC active month, 15 min, all hours |
| Checks | EMA 20/50 stack, EMA 200 side, ADX ≥ 20 + DI, RSI zone + direction, MACD histogram, pullback to EMA 20 within 5 bars, trigger bar, volume > 20-bar avg | same, indicators re-implemented with Pine's formulas (`src/goldbot/indicators.py`) |
| Session | 7:20–9:30 CT (bar open time) | same |
| Entry | next bar open after the signal bar | same |
| Stop / target | 1.75 × ATR(14) / 1.5 × stop, from the signal close | same |
| Exit | stop or target only, one trade at a time | same, incl. TradingView's same-bar high/low rule |
| News | skip 7:00–8:00 CT on CPI / jobs days | same list + this week's ForexFactory calendar |

`tests/fixtures/tradingview_gcz2026_trades.csv` holds TradingView's own trade list
(Jul 8 – Oct 12, 2026, 29 trades) so you can check the bot takes the same trades (step 6).

## Setup (Mac, about 15 minutes)

### 1. Install

```bash
git clone https://github.com/<you>/gold-signal-bot.git
cd gold-signal-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
cp .env.example .env
```

Needs Python 3.11+ (`python3 --version`; `brew install python@3.12` if older).

### 2. tastytrade API access (one time)

1. Log in at tastytrade → **Manage → My Profile → API** (the "OAuth Applications" page; you may
   need to opt in to API access first).
2. **Create an OAuth application**: tick the read / market-data scopes (no trading scope needed),
   callback `http://localhost:8000`. Save the **client secret**.
3. Open the app → **Manage → Create Grant** and save the **refresh token** (it doesn't expire).
4. Put both in `.env`:
   ```
   TT_SECRET=...
   TT_REFRESH=...
   ```
Your account needs real-time **futures** market data enabled (tastytrade asks you to sign the
CME data agreement the first time you open a futures quote).

### 3. Phone notifications (ntfy, free)

1. Install **ntfy** on your phone (App Store / Play Store).
2. Subscribe to a topic with a hard-to-guess name, e.g. `rakesh-gold-k39xq`.
3. Put the same name in `config.toml` → `[notify] ntfy_topic = "rakesh-gold-k39xq"`.
4. `goldbot test-notify` → the test message should appear on your phone.

(Telegram also works: set `telegram_token` and `telegram_chat_id`.)

### 4. Check the connection

```bash
goldbot check
```
Prints the contract it is watching, the last closed bar and today's 8-check table, the same table
you see on the TradingView chart.

### 5. Run it

```bash
goldbot run            # sends alerts
goldbot run --dry-run  # prints alerts only
```

Leave it running during trading days. To keep it alive without a terminal window, see
"Run in the background" below. Restarting is safe: alerts already sent are remembered in
`~/.goldbot/sent.json` and are not sent twice.

### 6. Parity check against TradingView (recommended once)

```bash
goldbot backtest --days 110 --compare tests/fixtures/tradingview_gcz2026_trades.csv
```
Lists every trade the bot would have taken, the win rate, and how many of TradingView's trades it
also took. Expect almost all of them. Small differences can come from where history starts
(indicator warm-up and whether a trade was already open) and from tiny volume differences between
data vendors. If many are missing, open an issue in this repo with the output.

## Run in the background (macOS)

`launchd` keeps it running and restarts it after a crash or reboot. Create
`~/Library/LaunchAgents/com.goldbot.plist` (adjust the two paths):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.goldbot</string>
  <key>WorkingDirectory</key><string>/Users/YOU/gold-signal-bot</string>
  <key>ProgramArguments</key><array>
    <string>/Users/YOU/gold-signal-bot/.venv/bin/goldbot</string><string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/goldbot.log</string>
  <key>StandardErrorPath</key><string>/tmp/goldbot.log</string>
</dict></plist>
```
```bash
launchctl load ~/Library/LaunchAgents/com.goldbot.plist     # start
tail -f /tmp/goldbot.log                                     # watch
launchctl unload ~/Library/LaunchAgents/com.goldbot.plist   # stop
```
The Mac must be awake at 7:20–9:30 CT (System Settings → Battery/Energy → prevent sleep when
plugged in, or `caffeinate -s` while it runs).

## Commands

| Command | What it does |
|---|---|
| `goldbot run [--dry-run]` | live alerts |
| `goldbot check` | connect + print the current checklist |
| `goldbot backtest --days N [--compare CSV] [--csv BARS]` | replay on tastytrade history (or a bars CSV) |
| `goldbot test-notify` | send a test push |

All read `config.toml` in the current folder (`-c path` to change).

## Contract rolls

`symbol = "auto"` follows tastytrade's active GC month, so the roll from December to February
happens by itself. To pin a contract, set e.g. `symbol = "/GCG7"`.

## News days

`[news_days]` in `config.toml` has the 2026 CPI and jobs-report dates (same as the TradingView
script). With `fetch_news_calendar = true` the bot also reads this week's calendar from
ForexFactory every day, so a missing or moved date is still caught. Add 2027 dates when the BLS
publishes them.

## Development

```bash
pytest           # indicator + strategy tests
```
Code map: `indicators.py` (Pine-equivalent indicators), `strategy.py` (checks + trade simulation),
`feed.py` (tastytrade candles), `news.py`, `notify.py`, `messages.py`, `cli.py`.

---
Educational tool, not financial advice. Backtests don't guarantee future results; paper trade first.
