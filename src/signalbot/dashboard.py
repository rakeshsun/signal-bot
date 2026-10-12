"""A self-contained HTML status page, rewritten every minute by the live bot.

Open logs/dashboard.html in a browser (it reloads itself every 60 s). No server needed.
"""

from __future__ import annotations

import html
import os
import time
from datetime import date, datetime
from pathlib import Path

from .bars import CHICAGO
from .strategies.base import Context

E = html.escape


def _t(ms_or_s: float, fmt: str = "%a %b %d %I:%M %p") -> str:
    v = ms_or_s / 1000 if ms_or_s > 1e11 else ms_or_s
    return datetime.fromtimestamp(v, tz=CHICAGO).strftime(fmt)


def _px(x: float | None) -> str:
    return "–" if x is None else (f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.1f}")


def _cell(v) -> str:
    if v is True:
        return '<span class="ok">✔</span>'
    if v is False:
        return '<span class="no">✘</span>'
    return "" if v is None else E(str(v))


def _watch_card(lv, nd) -> str:
    w, res = lv.watch, lv.res
    bars = lv.book.closed_bars()
    if res is None or not bars:
        return f'<section class="card"><h2>{E(w.name)}</h2><p class="muted">Waiting for data…</p></section>'
    last = bars[-1]
    rows = lv.strategy.checklist(bars, Context(lv.symbol, nd))
    two = any(r[2] is not None for r in rows)
    ck = "".join(
        f"<tr><td>{E(str(lab))}</td><td>{_cell(lo)}</td>{f'<td>{_cell(sh)}</td>' if two else ''}</tr>" for lab, lo, sh in rows
    )
    ck_head = "<tr><th>Check</th><th>Long</th><th>Short</th></tr>" if two else "<tr><th>Check</th><th>Now</th></tr>"

    t = res.open_trade
    if t:
        side = "LONG" if t.direction > 0 else "SHORT"
        upnl = (last.close - (t.entry_price or t.signal_close)) * t.direction
        money = " · ".join(f"{E(n)} {'+' if upnl >= 0 else '−'}${abs(upnl) * ppt:,.0f}" for n, ppt in w.contracts.items())
        trade = (
            f'<div class="trade {"long" if t.direction > 0 else "short"}"><div class="side">{side}</div>'
            f"<div>since {_t(t.signal_time)} · entry {_px(t.entry_price or t.signal_close)}</div>"
            f"<div>SL <b>{_px(t.stop)}</b> · TP <b>{_px(t.target)}</b></div>"
            f'<div class="{"ok" if upnl >= 0 else "no"}">now {_px(last.close)} → {"+" if upnl >= 0 else ""}{upnl:.2f} pts'
            f"{(' · ' + money) if money else ''}</div></div>"
        )
    else:
        trade = '<div class="trade flat">No open trade</div>'

    closed = [x for x in res.trades if not x.is_open]
    wins = sum(1 for x in closed if x.won)
    gw = sum(x.points for x in closed if (x.points or 0) > 0)
    gl = -sum(x.points for x in closed if (x.points or 0) <= 0)
    pf = f"{gw / gl:.2f}" if gl else "–"
    trs = "".join(
        f"<tr><td>{_t(x.signal_time, '%b %d %H:%M')}</td><td>{'LONG' if x.direction > 0 else 'SHORT'}</td>"
        f"<td>{_px(x.entry_price)}</td><td>{_px(x.exit_price)}</td><td>{E(x.exit_reason or 'open')}</td>"
        f"<td class=\"{'ok' if (x.points or 0) > 0 else 'no'}\">{'' if x.points is None else f'{x.points:+.2f}'}</td></tr>"
        for x in reversed(res.trades[-15:])
    )
    stats = (f"{wins}/{len(closed)} won ({100 * wins / len(closed):.0f}%) · profit factor {pf}" if closed else "no closed trades yet")
    return f"""
<section class="card">
  <h2>{E(w.name)} <span class="muted">· {E(lv.symbol)} · {E(w.strategy)} · {E(w.interval)}</span></h2>
  <p class="muted">Last closed bar {_t(last.time)} CT · close <b>{_px(last.close)}</b> · {len(bars)} bars loaded{f' · stream restarts {lv.restarts}' if lv.restarts else ''}</p>
  <div class="grid">
    <div>{trade}<table class="ck">{ck_head}{ck}</table></div>
    <div><h3>Recent trades <span class="muted">({E(stats)}, last {len(bars) * lv.book.bar_ms // 86_400_000} days)</span></h3>
    <table class="tr"><tr><th>Signal</th><th>Side</th><th>Entry</th><th>Exit</th><th>Result</th><th>Pts</th></tr>{trs}</table></div>
  </div>
</section>"""


def render(lives, nd: dict[date, str], alerts: list[dict], started: float, mkt_open: bool) -> str:
    today = datetime.now(CHICAGO).date()
    upcoming = sorted((d, n) for d, n in nd.items() if d >= today)[:6]
    news_html = ", ".join(f"{d:%a %b %d} {E(n)}" for d, n in upcoming) or "none listed"
    al = "".join(
        f'<tr class="k-{E(a.get("kind", "info"))}"><td>{_t(a["t"], "%b %d %I:%M %p")}</td><td><b>{E(a["title"])}</b>'
        f'<div class="muted pre">{E(a["body"])}</div></td></tr>'
        for a in reversed(alerts[-25:])
    ) or '<tr><td class="muted">No alerts yet</td></tr>'
    cards = "".join(_watch_card(lv, nd) for lv in lives)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Signal Bot</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#6b7385;--line:#e3e6ec;--ok:#15803d;--no:#c2410c;--long:#e8f6ee;--short:#fdeee6;--accent:#2f5bd3}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1218;--card:#171b23;--fg:#e6e9ef;--muted:#8d95a6;--line:#262c38;--ok:#4ade80;--no:#fb923c;--long:#12291c;--short:#2d1a10;--accent:#7aa2ff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
header{{padding:18px 20px 6px}}h1{{margin:0;font-size:20px}}h2{{margin:0 0 4px;font-size:17px}}h3{{margin:0 0 6px;font-size:14px}}
main{{padding:8px 20px 30px;display:grid;gap:14px;max-width:1200px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.muted{{color:var(--muted);font-weight:normal}}.ok{{color:var(--ok)}}.no{{color:var(--no)}}
.pill{{display:inline-block;padding:2px 9px;border-radius:99px;border:1px solid var(--line);margin-right:6px;font-size:12px}}
.grid{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:16px;margin-top:8px}}
@media (max-width:760px){{.grid{{grid-template-columns:minmax(0,1fr)}}}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600;font-size:12px}}.ck td:nth-child(n+2),.ck th:nth-child(n+2){{text-align:center;width:60px}}
.tr td:nth-child(n+3){{font-variant-numeric:tabular-nums}}
.trade{{border-radius:8px;padding:10px 12px;margin-bottom:10px;border:1px solid var(--line)}}.trade .side{{font-weight:700}}
.trade.long{{background:var(--long)}}.trade.short{{background:var(--short)}}.trade.flat{{color:var(--muted)}}
.pre{{white-space:pre-wrap;font-size:12px}}.al td:first-child{{width:120px;white-space:nowrap}}.k-entry td{{background:color-mix(in srgb,var(--accent) 8%,transparent)}}
</style></head><body>
<header><h1>Signal Bot</h1>
<p class="muted"><span class="pill">{'🟢 market open' if mkt_open else '⚪ market closed'}</span>
<span class="pill">running since {_t(started)}</span><span class="pill">updated {_t(time.time(), '%I:%M:%S %p')} CT</span></p>
<p class="muted">Upcoming news days (no gold entries 7:00–8:00 CT): {news_html}</p></header>
<main>{cards}
<section class="card"><h2>Alerts</h2><table class="al">{al}</table></section>
<p class="muted">Signals only, not advice. This page refreshes every minute while the bot runs; if the "updated" time is old, the bot has stopped.</p>
</main></body></html>"""


def write(path: Path, lives, nd, alerts, started, mkt_open) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render(lives, nd, alerts, started, mkt_open), encoding="utf-8")
    os.replace(tmp, path)
