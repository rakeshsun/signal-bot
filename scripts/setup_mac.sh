#!/usr/bin/env bash
# One-time setup on macOS: virtualenv, install, config files, tests.
# Run from the repo root:  ./scripts/setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11+ not found. Install it with:  brew install python@3.12   then re-run this script." >&2
  exit 1
fi
echo "Using $($PY --version) ($PY)"

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"

[ -f config.toml ] || { cp config.example.toml config.toml; echo "Created config.toml"; }
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Created .env (add your tastytrade keys)"; }

.venv/bin/pytest -q

cat <<'MSG'

Setup done. Next:
  1. Edit .env          -> TT_SECRET and TT_REFRESH from your tastytrade OAuth app (README step 2)
  2. Edit config.toml   -> [notify] ntfy_topic = "<your topic>"  (ntfy app by Philipp Heckel)
  3. source .venv/bin/activate
     signalbot test-notify
     signalbot check
     signalbot backtest gold --days 110 --compare tests/fixtures/tradingview_gcz2026_trades.csv
     signalbot run
MSG
