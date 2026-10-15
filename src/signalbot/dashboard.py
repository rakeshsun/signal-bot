"""A self-contained HTML status page, rewritten every minute by the live bot.

Open logs/dashboard.html in a browser (it reloads itself every 60 s). No server needed.
Layout per watch: today's position with a price ladder (stop / entry / now / target),
the morning session timeline, the 8-check readiness meters, recent trades; then alerts.
"""

from __future__ import annotations

import html
import os
import time
from datetime import date, datetime
from pathlib import Path

from .bars import CHICAGO
from .messages import contract_names
from .strategies.base import Context

E = html.escape


def _dt(ms_or_s: float) -> datetime:
    v = ms_or_s / 1000 if ms_or_s > 1e11 else ms_or_s
    return datetime.fromtimestamp(v, tz=CHICAGO)


def _clock(ms_or_s: float) -> str:
    return _dt(ms_or_s).strftime("%-I:%M %p")


def _day(ms_or_s: float) -> str:
    d = _dt(ms_or_s)
    today = datetime.now(CHICAGO).date()
    if d.date() == today:
        return "Today"
    return d.strftime("%a %b %-d") if (today - d.date()).days < 6 else d.strftime("%b %-d")


def _px(x: float | None) -> str:
    return "–" if x is None else (f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.1f}")


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}${abs(x):,.0f}"


# ───────────────────────── pieces ─────────────────────────


def _ladder(t, last_close: float) -> str:
    """Vertical price ladder: target, entry, now, stop placed by price."""
    entry = t.entry_price or t.signal_close
    pts = {"Target": t.target, "Entry": entry, "Now": last_close, "Stop": t.stop}
    hi, lo = max(pts.values()), min(pts.values())
    span = (hi - lo) or 1.0

    def y(v: float) -> float:
        return 6 + (hi - v) / span * 88  # % from top

    zone_good = (min(entry, t.target), max(entry, t.target))
    zone_bad = (min(entry, t.stop), max(entry, t.stop))
    rows = "".join(
        f'<div class="lv lv-{k.lower()}" style="top:{y(v):.1f}%"><span class="lv-k">{k}</span><span class="lv-v">{_px(v)}</span></div>'
        for k, v in pts.items()
    )
    return f"""<div class="ladder" role="img" aria-label="Stop {_px(t.stop)}, entry {_px(entry)}, now {_px(last_close)}, target {_px(t.target)}">
  <div class="zone good" style="top:{y(zone_good[1]):.1f}%;height:{y(zone_good[0]) - y(zone_good[1]):.1f}%"></div>
  <div class="zone bad" style="top:{y(zone_bad[1]):.1f}%;height:{y(zone_bad[0]) - y(zone_bad[1]):.1f}%"></div>
  {rows}
</div>"""


def _live(lv):
    """(price, bar) of the bar in progress, or (None, None) outside trading hours."""
    f = lv.book.forming() if hasattr(lv.book, "forming") else None
    return (f.close, f) if f else (None, None)


def _position(lv, last) -> str:
    t = lv.res.open_trade
    live_px, live_bar = _live(lv)
    now_px = live_px if live_px is not None else last.close
    names = contract_names(lv.watch, lv.symbol)
    if not t:
        return f"""<div class="pos flat">
  <p class="pos-state">Flat</p>
  <p class="pos-sub">No open trade on {E(lv.symbol)}.</p>
  <p class="price">{_px(now_px)}<span>{'live price' if live_px is not None else f'last close, {_clock(last.time + lv.book.bar_ms)} (market closed)'}</span></p>
</div>"""
    up = (now_px - (t.entry_price or t.signal_close)) * t.direction
    side = "Long" if t.direction > 0 else "Short"
    money = "".join(f"<li><b>{E(n)}</b> {_usd(up * ppt)}</li>" for n, ppt in names.items())
    return f"""<div class="pos {'long' if t.direction > 0 else 'short'}">
  <div class="pos-main">
    <p class="pos-state">{side} {E(lv.symbol)}</p>
    <p class="pos-sub">Signal {_day(t.signal_time).lower() if _day(t.signal_time) == 'Today' else _day(t.signal_time)} at {_clock(t.signal_time + lv.book.bar_ms)}, entry {_px(t.entry_price or t.signal_close)}</p>
    <p class="pnl {'up' if up >= 0 else 'down'}">{up:+.2f}<span>points open, {'live' if live_px is not None else 'at last close'} {_px(now_px)}</span></p>
    <ul class="money">{money}</ul>
    <p class="pos-sub">Stop {t.risk:.2f} pts away, target {t.reward:.2f} pts away</p>
  </div>
  {_ladder(t, now_px)}
</div>"""


def _window(lv) -> str:
    p = lv.strategy.p
    a = getattr(p, "session_start", getattr(p, "from_hhmm", None))
    b = getattr(p, "session_end", getattr(p, "to_hhmm", None))
    if a is None or b is None:
        return "its session hours"
    f = lambda h: datetime(2000, 1, 1, h // 100, h % 100).strftime("%-I:%M %p")  # noqa: E731
    return f"{f(a)} and {f(b)} CT"


def _timeline(lv, news_today: str | None) -> str:
    """Morning strip 6:00-11:00 CT with the entry window, news block and a now marker."""
    p = lv.strategy.p
    a = getattr(p, "session_start", getattr(p, "from_hhmm", None))
    b = getattr(p, "session_end", getattr(p, "to_hhmm", None))
    if a is None or b is None:
        return ""
    night = a > b  # overnight window (e.g. Asia): show 4 PM - 4 AM instead of the morning
    start, end = (16 * 60, 28 * 60) if night else (6 * 60, 12 * 60)

    def mins(hhmm: int) -> int:
        m = hhmm // 100 * 60 + hhmm % 100
        return m + 24 * 60 if night and m < start else m

    def x(hhmm: int) -> float:
        return max(0.0, min(100.0, (mins(hhmm) - start) / (end - start) * 100))

    now = datetime.now(CHICAGO)
    now_m = now.hour * 100 + now.minute
    showing_now = start <= mins(now_m) < end and (night or now.weekday() < 5)
    marker = f'<div class="tl-now" style="left:{x(now_m):.1f}%"><span>now</span></div>' if showing_now else ""
    news = (f'<div class="tl-news" style="left:{x(700):.1f}%;width:{x(800) - x(700):.1f}%" title="{E(news_today)} at 7:30, no entries"></div>'
            if news_today and lv.watch.news_alert else "")
    hours = range(16, 29, 2) if night else range(6, 13)
    ticks = "".join(f'<span style="left:{x((h % 24) * 100):.1f}%">{(h % 12) or 12}{"am" if h % 24 < 12 else "pm"}</span>' for h in hours)
    recent = now.timestamp() * 1000 - 14 * 3600_000
    trades = "".join(
        f'<div class="tl-trade {"long" if t.direction > 0 else "short"}" style="left:{x(_dt(t.signal_time).hour * 100 + _dt(t.signal_time).minute):.1f}%" title="{"Long" if t.direction > 0 else "Short"} signal {_clock(t.signal_time + lv.book.bar_ms)}"></div>'
        for t in lv.res.trades if (t.signal_time >= recent if night else _dt(t.signal_time).date() == now.date())
    )
    if night:
        news_today = None
    return f"""<div class="tl">
  <div class="tl-track"><div class="tl-win" style="left:{x(a):.1f}%;width:{x(b) - x(a):.1f}%"></div>{news}{trades}{marker}</div>
  <div class="tl-ticks">{ticks}</div>
  <p class="tl-cap">Entry window {_window(lv)}{' (overnight)' if night else ''}{f'. {E(news_today)} at 7:30: no entries 7–8' if news_today and lv.watch.news_alert else ''}</p>
</div>"""


def _meter(label: str, oks: list[bool], names: list[str], side: str) -> str:
    n = sum(oks)
    segs = "".join(f'<i class="{"on" if ok else ""}" title="{E(nm)}: {"yes" if ok else "no"}"></i>' for ok, nm in zip(oks, names))
    return f'<div class="meter {side}{" ready" if n == len(oks) else ""}"><span class="m-l">{label}</span><span class="m-s">{segs}</span><span class="m-n">{n}/{len(oks)}</span></div>'


def _bool_rows(rows):
    return [r for r in rows if isinstance(r[1], bool) and isinstance(r[2], bool)]


def _missing(rows, side: int) -> str:
    miss = [str(r[0]) for r in rows if not r[side]]
    return "all 8 pass" if not miss else ("needs " + ", ".join(miss[:3]) + (f" and {len(miss) - 3} more" if len(miss) > 3 else ""))


def _checks(lv, nd) -> str:
    closed = lv.book.closed_bars()
    ctx = Context(lv.symbol, nd)
    rows = lv.strategy.checklist(closed, ctx)
    brows = _bool_rows(rows)
    other = [r for r in rows if r not in brows and not str(r[0]).startswith("Checks passed")]
    out = []
    _, live_bar = _live(lv)
    plain = lv.strategy.summary(closed, ctx, live_bar)
    sig = _signal_button(lv)
    if sig:
        out.append(sig)
    elif any(hot for _, _, hot in plain):
        side = "SELL" if any(hot and lab.startswith("SELL") for lab, _, hot in plain) else "BUY"
        out.append(f'<p class="ready-badge {side.lower()}">GET READY · {side} possible</p>')
    if plain:
        other = []
        if not sig:
            out.append('<div class="plain">' + "".join(
                f'<p class="kv{" go" if hot else ""}"><span>{E(lab)}</span><b>{E(txt)}</b></p>' for lab, txt, hot in plain) + '</div>')
        if brows:
            out.append(f'<details class="more" data-key="d:{E(lv.watch.name)}"><summary>Show the details</summary>')
    if brows:
        names = [str(r[0]) for r in brows]
        if live_bar is not None:
            prow = _bool_rows(lv.strategy.checklist(closed + [live_bar], ctx))
            if prow:
                closes = _clock(live_bar.time + lv.book.bar_ms)
                out.append(f'<div class="live"><h4>Live: if the bar closed now <span>(it closes {closes})</span></h4>'
                           + _meter("Long", [r[1] for r in prow], names, "long")
                           + _meter("Short", [r[2] for r in prow], names, "short")
                           + f'<p class="miss">Long {E(_missing(prow, 1))}.<br>Short {E(_missing(prow, 2))}.</p>'
                           + '<p class="muted small">Changes with every tick. Only the closed bar counts for a signal.</p></div>')
        out.append(f'<h4>Last closed bar <span>({_clock(closed[-1].time + lv.book.bar_ms)})</span></h4>')
        out.append(_meter("Long", [r[1] for r in brows], names, "long"))
        out.append(_meter("Short", [r[2] for r in brows], names, "short"))
        items = "".join(
            f'<li><span class="{"y" if r[1] else "n"}" aria-label="long {"yes" if r[1] else "no"}"></span>'
            f'<span class="{"y" if r[2] else "n"}" aria-label="short {"yes" if r[2] else "no"}"></span>{E(str(r[0]))}</li>'
            for r in brows
        )
        out.append(f'<details><summary>The {len(brows)} checks</summary><ul class="cklist"><li class="hd"><span>L</span><span>S</span>check</li>{items}</ul></details>')
        wl, nl = lv.res.win_rate(1)
        ws, ns = lv.res.win_rate(-1)
        out.append(f'<p class="hist">When all 8 lined up recently: longs won <b>{wl}/{nl}</b>, shorts won <b>{ws}/{ns}</b>. Past results, not a forecast.</p>')
        if plain:
            out.append('</details>')
    for lab, lo, sh in other:
        v = lo if not isinstance(lo, bool) else ("yes" if lo else "no")
        out.append(f'<p class="kv"><span>{E(str(lab))}</span><b>{E(str(v))}</b></p>')
    return "".join(out)


def _trades(lv) -> str:
    res = lv.res
    closed = [t for t in res.trades if not t.is_open]
    if not res.trades:
        return '<p class="muted">No trades in the loaded history yet.</p>'
    wins = sum(1 for t in closed if t.won)
    gw = sum(t.points for t in closed if (t.points or 0) > 0)
    gl = -sum(t.points for t in closed if (t.points or 0) <= 0)
    net = gw - gl
    days = max(1, len(res.bars) * lv.book.bar_ms // 86_400_000)
    names = contract_names(lv.watch, lv.symbol)
    first = next(iter(names.items()), None)
    summary = (
        f'<div class="tsum"><div><b>{wins}/{len(closed)}</b><span>won</span></div>'
        f'<div><b>{(gw / gl if gl else 0):.2f}</b><span>profit factor</span></div>'
        f'<div><b class="{"up" if net >= 0 else "down"}">{net:+.1f}</b><span>points</span></div>'
        + (f'<div><b class="{"up" if net >= 0 else "down"}">{_usd(net * first[1])}</b><span>per {E(first[0])}</span></div>' if first else "")
        + "</div>"
    )
    def row(t):
        return (
        f'<tr><td>{_day(t.signal_time)}<small>{_clock(t.signal_time + lv.book.bar_ms)}</small></td>'
        f'<td><span class="tag {"long" if t.direction > 0 else "short"}">{"Long" if t.direction > 0 else "Short"}</span></td>'
        f'<td class="num">{_px(t.entry_price or t.signal_close)}</td><td class="num">{_px(t.exit_price)}</td>'
        f'<td>{E({"target": "Target", "stop": "Stop", "time": "Time exit"}.get(t.exit_reason or "", "Open"))}</td>'
        f'<td class="num {"up" if (t.points or 0) > 0 else "down" if t.points is not None else ""}">{"" if t.points is None else f"{t.points:+.2f}"}</td></tr>')

    recent = list(reversed(res.trades[-12:]))
    head = '<thead><tr><th>Signal</th><th>Side</th><th class="num">Entry</th><th class="num">Exit</th><th>Result</th><th class="num">Pts</th></tr></thead>'
    plural = "trades" if len(recent) > 5 else "trade"
    more = (f'<details class="more" data-key="o:{E(lv.watch.name)}"><summary>Show {len(recent) - 4} older {plural}</summary><table class="tt">{head}<tbody>'
            + "".join(row(t) for t in recent[4:]) + '</tbody></table></details>') if len(recent) > 4 else ""
    return f"""<h3>Last {days} days</h3>{summary}
<table class="tt">{head}<tbody>{"".join(row(t) for t in recent[:4])}</tbody></table>{more}"""


def _watch(lv, nd) -> str:
    w = lv.watch
    bars = lv.book.closed_bars()
    if lv.res is None or not bars:
        return f'<section class="watch"><h2>{E(w.name.title())}</h2><p class="muted">Waiting for market data.</p></section>'
    last = bars[-1]
    now_ct = datetime.now(CHICAGO)
    news_today = nd.get(now_ct.date()) if now_ct.hour < 8 else None  # the 7:00-8:00 block is over after 8
    t = lv.res.open_trade
    inwin = _in_window(lv, now_ct)
    live_px, _ = _live(lv)
    px = live_px if live_px is not None else last.close
    last_t = lv.res.trades[-1] if lv.res.trades else None
    fresh_exit = bool(last_t and last_t.exit_time and time.time() * 1000 - (last_t.exit_time + lv.book.bar_ms) <= FRESH_MIN * 60_000)
    if t:
        state, cls = f"{'Long' if t.direction > 0 else 'Short'} since {_clock(t.signal_time + lv.book.bar_ms)}", "long" if t.direction > 0 else "short"
    elif inwin:
        state, cls = "Watching now", "on"
    else:
        state, cls = f"Next window {_window(lv).replace(' and ', '–')}", ""
    is_open = bool(t or inwin or fresh_exit)
    force = ' data-force="1"' if t and time.time() * 1000 - (t.signal_time + lv.book.bar_ms) <= FRESH_MIN * 60_000 else ""
    key = f"w:{w.name}:{'open' if is_open else 'shut'}"
    return f"""<details class="watch {cls}" data-key="{E(key)}"{force}{' open' if is_open else ''}>
  <summary class="wh"><span class="w-name">{E(w.name.title())}{' <span class="paper">paper</span>' if w.paper else ''}</span>
    <span class="w-state {cls}">{E(state)}</span><span class="w-px">{_px(px)}</span><span class="chev" aria-hidden="true"></span></summary>
  <div class="w-body">
  <p class="w-meta">{E(lv.symbol)} on {E(w.interval)} bars, strategy {E(w.strategy.replace('_', ' '))}</p>
  <div class="cols">
    <div class="col-a">{_position(lv, last)}{_timeline(lv, news_today)}</div>
    <div class="col-b"><h3>Ready to enter?</h3>{_checks(lv, nd)}</div>
  </div>
  <div class="trades">{_trades(lv)}</div>
  </div>
</details>"""


def _alerts(alerts: list[dict]) -> str:
    if not alerts:
        return '<p class="muted">Nothing sent yet. Trade signals, exits, news-day warnings and the 7:15 check-in will show here.</p>'
    today, earlier = [], []
    for a in reversed(alerts[-25:]):
        body = E(a["body"]).replace("\n", "<br>")
        is_today = _day(a["t"]) == "Today"
        (today if is_today else earlier).append(
            f'<li class="al {E(a.get("kind", "info"))}"><time>{_day(a["t"])}<br>{_clock(a["t"])}</time>'
            f'<details{" open" if is_today and a.get("kind") in ("entry", "exit") else ""}><summary>{E(a["title"])}</summary><p>{body}</p></details></li>'
        )
    out = f'<ol class="alerts">{"".join(today)}</ol>' if today else '<p class="muted">Nothing sent today.</p>'
    if earlier:
        out += (f'<details class="earlier" data-key="alerts-earlier"><summary>Earlier ({len(earlier)})</summary>'
                f'<ol class="alerts">{"".join(earlier)}</ol></details>')
    return out


CSS = """
:root{--bg:#eef0ec;--paper:#fbfbf8;--ink:#1d2427;--soft:#5d676b;--line:#d8dcd4;--gold:#a8822a;--gold-2:#e9d9a6;
--up:#2c7a55;--down:#b0412e;--up-bg:#e3f1e8;--down-bg:#f6e3dd;--news:#e7c46a}
@media (prefers-color-scheme:dark){:root{--bg:#121516;--paper:#1a1e20;--ink:#e9e6dc;--soft:#9aa29f;--line:#2b3133;--gold:#d6b35c;--gold-2:#4a3f1f;
--up:#5fc68f;--down:#ef7a63;--up-bg:#173225;--down-bg:#3a1f19;--news:#a9862f}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Archivo",system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-width:1120px;margin:0 auto;padding:28px 20px 48px}
.top{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px 24px;margin-bottom:18px}
.top h1{margin:0;font:700 26px/1.1 "Archivo",sans-serif;font-stretch:115%;letter-spacing:-.01em}
.top h1 em{font-style:normal;color:var(--gold)}
.status{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--soft);font-size:13px;margin:0}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--soft);margin-right:6px;vertical-align:1px}
.dot.on{background:var(--up);box-shadow:0 0 0 3px color-mix(in srgb,var(--up) 25%,transparent)}
.stale{color:var(--down);font-weight:600}
.newsbar{background:var(--paper);border:1px solid var(--line);border-left:4px solid var(--news);border-radius:6px;padding:10px 14px;margin:0 0 18px;font-size:14px}
.watch{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:22px 22px 18px;margin-bottom:18px}
.wh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;margin-bottom:16px}
.wh h2{margin:0;font:700 22px/1.1 "Archivo",sans-serif;font-stretch:115%}.wh p{margin:0;color:var(--soft);font-size:13px}
.cols{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:22px}
@media (max-width:820px){.cols{grid-template-columns:minmax(0,1fr)}}
h3{font-size:14px;margin:0 0 10px;color:var(--soft);font-weight:600}
.pos{display:grid;grid-template-columns:minmax(0,1fr) 170px;gap:14px;border-radius:10px;padding:16px 16px 14px;border:1px solid var(--line)}
.pos.long{background:linear-gradient(0deg,var(--paper),var(--up-bg))}.pos.short{background:linear-gradient(0deg,var(--paper),var(--down-bg))}
.pos.flat{display:block}
.pos p{margin:0}.pos-state{font:700 20px/1.2 "Archivo",sans-serif;font-stretch:112%}
.pos.long .pos-state{color:var(--up)}.pos.short .pos-state{color:var(--down)}
.pos-sub{color:var(--soft);font-size:13px;margin-top:2px!important}
.pnl,.price{font:600 44px/1 "Archivo",sans-serif;font-stretch:118%;margin:14px 0 6px!important;letter-spacing:-.02em}
.pnl span,.price span{display:block;font:500 12px/1.4 "Archivo",sans-serif;font-stretch:100%;color:var(--soft);letter-spacing:0;margin-top:4px}
.up{color:var(--up)}.down{color:var(--down)}
.money{list-style:none;padding:0;margin:0 0 8px;display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px}
.ladder{position:relative;min-height:190px;border-left:2px solid var(--line);margin-left:6px}
.zone{position:absolute;left:0;right:0}.zone.good{background:color-mix(in srgb,var(--up) 14%,transparent)}.zone.bad{background:color-mix(in srgb,var(--down) 14%,transparent)}
.lv{position:absolute;left:-2px;right:0;transform:translateY(-50%);display:flex;justify-content:space-between;align-items:center;font-size:12px;padding-left:10px;border-top:1px dashed var(--soft)}
.lv-k{color:var(--soft);background:inherit}.lv-v{font-weight:600}
.lv-target{border-top-color:var(--up)}.lv-stop{border-top-color:var(--down)}.lv-target .lv-v{color:var(--up)}.lv-stop .lv-v{color:var(--down)}
.lv-now{border-top:2px solid var(--gold)}.lv-now .lv-k,.lv-now .lv-v{color:var(--gold);font-weight:700}
.lv-now::before{content:"";position:absolute;left:-6px;top:-6px;width:10px;height:10px;border-radius:50%;background:var(--gold)}
.tl{margin-top:16px}
.tl-track{position:relative;height:16px;border-radius:8px;background:color-mix(in srgb,var(--line) 70%,transparent)}
.tl-win{position:absolute;top:0;bottom:0;border-radius:8px;background:var(--gold-2);border:1px solid var(--gold)}
.tl-news{position:absolute;top:-3px;bottom:-3px;border-radius:4px;background:repeating-linear-gradient(135deg,var(--news) 0 4px,transparent 4px 8px);opacity:.9}
.tl-now{position:absolute;top:-6px;bottom:-6px;width:2px;background:var(--ink)}
.tl-now span{position:absolute;top:-16px;left:-11px;font-size:11px;color:var(--ink)}
.tl-trade{position:absolute;top:3px;width:10px;height:10px;margin-left:-5px;border-radius:2px;transform:rotate(45deg)}
.tl-trade.long{background:var(--up)}.tl-trade.short{background:var(--down)}
.tl-ticks{position:relative;height:16px;font-size:11px;color:var(--soft)}.tl-ticks span{position:absolute;transform:translateX(-50%)}
.tl-ticks span:first-child{transform:none}.tl-ticks span:last-child{transform:translateX(-100%)}
.tl-cap{margin:6px 0 0;font-size:13px;color:var(--soft)}
.meter{display:grid;grid-template-columns:48px minmax(0,1fr) 34px;align-items:center;gap:10px;margin-bottom:10px}
.m-l{font-weight:600}.m-n{text-align:right;font-weight:600;color:var(--soft)}
.m-s{display:grid;grid-template-columns:repeat(8,1fr);gap:4px}
.m-s i{height:22px;border-radius:4px;background:color-mix(in srgb,var(--line) 80%,transparent)}
.meter.long .m-s i.on{background:var(--up)}.meter.short .m-s i.on{background:var(--down)}
.meter.ready .m-n{color:var(--gold)}.meter.ready .m-s{outline:2px solid var(--gold);outline-offset:3px;border-radius:5px}
details summary{cursor:pointer;color:var(--soft);font-size:13px;margin:4px 0 6px}
details summary:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
.cklist{list-style:none;margin:0;padding:0;font-size:14px}
.cklist li{display:grid;grid-template-columns:22px 22px minmax(0,1fr);align-items:center;padding:4px 0;border-bottom:1px solid var(--line)}
.cklist li.hd{color:var(--soft);font-size:12px}
.cklist .y::before{content:"✔";color:var(--up)}.cklist .n::before{content:"·";color:var(--soft);font-weight:700}
.kv{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:6px 0;margin:0}
.plain .kv{display:block;padding:8px 0}.plain .kv span{display:block;color:var(--soft);font-size:13px;margin-bottom:2px}.plain .kv b{display:block;text-align:left;font-weight:600;line-height:1.4}.plain .kv.go b{color:var(--up)}.plain{margin-bottom:10px}details.more>summary{cursor:pointer;color:var(--soft);font-size:13px;margin:6px 0}
.trades{margin-top:22px;border-top:1px solid var(--line);padding-top:16px}
.tsum{display:flex;flex-wrap:wrap;gap:8px 28px;margin-bottom:10px}
.tsum div{display:flex;flex-direction:column}.tsum b{font:600 22px/1.1 "Archivo",sans-serif;font-stretch:112%}.tsum span{font-size:12px;color:var(--soft)}
.tt{width:100%;border-collapse:collapse;font-size:14px;table-layout:fixed}.tt th:nth-child(1){width:22%}.tt th:nth-child(2){width:14%}.tt th:nth-child(3),.tt th:nth-child(4){width:17%}.tt th:nth-child(5){width:15%}.tt th:nth-child(6){width:15%}
.quiet{display:flex;flex-wrap:wrap;align-items:center;gap:8px;border:1px solid var(--line);border-radius:14px;padding:12px 18px;background:var(--paper)}.q-word{font-weight:800;color:var(--soft);margin-right:6px;font-stretch:110%;letter-spacing:.02em;text-transform:uppercase;font-size:13px}.chip{border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:13px;color:var(--soft)}.chip b{color:var(--ink);font-weight:700}.chip.on{border-color:var(--gold);background:color-mix(in srgb,var(--gold) 10%,transparent)}
.tt th{font-weight:500;color:var(--soft);font-size:12px;text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
.tt td{padding:7px 8px;border-bottom:1px solid var(--line)}.tt td:first-child{white-space:nowrap}.tt small{display:block;color:var(--soft);font-size:12px}
.num{text-align:right!important}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600}
.tag.long{background:var(--up-bg);color:var(--up)}.tag.short{background:var(--down-bg);color:var(--down)}
@media (max-width:560px){.tt th:nth-child(4),.tt td:nth-child(4){display:none}.pos{grid-template-columns:minmax(0,1fr)}.ladder{min-height:150px}}
.panel{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:20px 22px}
.panel h2{margin:0 0 12px;font:700 18px/1.2 "Archivo",sans-serif;font-stretch:112%}
.alerts{list-style:none;margin:0;padding:0}
.al{display:grid;grid-template-columns:88px minmax(0,1fr);gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}
.al time{font-size:12px;color:var(--soft);line-height:1.35}
.al summary{color:var(--ink);font-weight:600;font-size:14px;margin:0}
.al p{margin:6px 0 0;font-size:13px;color:var(--soft)}
.al.entry summary{color:var(--gold)}.al.warning summary{color:var(--down)}
.muted{color:var(--soft)}
.paper{font-size:12px;font-weight:600;vertical-align:middle;border:1px solid var(--soft);color:var(--soft);border-radius:99px;padding:1px 8px;margin-left:6px;font-stretch:100%}
h4{margin:12px 0 8px;font-size:13px;font-weight:600}h4 span{color:var(--soft);font-weight:400}
.live{border:1px dashed var(--gold);border-radius:10px;padding:4px 12px 10px;margin-bottom:6px;background:color-mix(in srgb,var(--gold-2) 30%,transparent)}
.miss{font-size:13px;margin:4px 0 2px}.small{font-size:12px;margin:2px 0 0}.hist{font-size:13px;color:var(--soft);margin:12px 0 0}

.acts{display:grid;gap:12px;margin:0 0 18px}
.act{display:grid;grid-template-columns:minmax(150px,auto) minmax(0,1fr);gap:6px 22px;align-items:center;border-radius:14px;padding:16px 22px;border:1px solid var(--line);background:var(--paper)}
.act p{margin:0}.act-word{font:800 46px/1 "Archivo",sans-serif;font-stretch:125%;letter-spacing:.01em}
.act-head{font-weight:700;font-size:16px}.act-body p+p{margin-top:4px}.act-sm{opacity:.85;font-size:13px}
.act.flash{color:#fff;border:0}.act.flash .act-word{font-size:64px}
.act.flash.buy{background:var(--up);--fl:var(--up)}.act.flash.sell{background:var(--down);--fl:var(--down)}
.act.hold .act-word{color:var(--gold)}.act.hold.buy{border-left:6px solid var(--up)}.act.hold.sell{border-left:6px solid var(--down)}
.act.exit.win .act-word{color:var(--up)}.act.exit.loss .act-word{color:var(--down)}
.act.none .act-word{color:var(--soft);font-size:32px;font-stretch:110%}
@media (max-width:560px){.act{grid-template-columns:minmax(0,1fr)}.act.flash .act-word{font-size:48px}}
@media (prefers-reduced-motion:no-preference){.act.flash{animation:flash 1s steps(2,jump-none) infinite alternate}
@keyframes flash{from{background:var(--fl);box-shadow:0 0 0 0 transparent}to{background:color-mix(in srgb,var(--fl) 55%,var(--gold));box-shadow:0 0 0 6px color-mix(in srgb,var(--gold) 45%,transparent)}}}

.foot{color:var(--soft);font-size:12px;margin-top:20px}
.sigbtn{display:flex;flex-direction:column;align-items:center;gap:4px;border-radius:14px;padding:16px 14px;margin:0 0 12px;color:#fff;text-align:center}.sigbtn.buy{background:var(--up);--fl:var(--up)}.sigbtn.sell{background:var(--down);--fl:var(--down)}.sigbtn .sb-word{font:800 34px/1 "Archivo",sans-serif;font-stretch:120%;letter-spacing:.02em}.sigbtn .sb-lv{font-weight:700;font-size:15px}.sigbtn .sb-sm{font-size:12px;opacity:.9}.sigbtn.hold{background:var(--paper);color:var(--ink);border:2px solid var(--fl)}.sigbtn.hold .sb-word{color:var(--fl);font-size:26px}.ready-badge{display:inline-block;margin:0 0 10px;padding:6px 14px;border-radius:999px;font-weight:800;font-size:14px;letter-spacing:.03em;border:2px solid var(--up);color:var(--up)}.ready-badge.sell{border-color:var(--down);color:var(--down)}
@media (prefers-reduced-motion:no-preference){.sigbtn.flash{animation:flash 1s steps(2,jump-none) infinite alternate}.ready-badge{animation:ready 1.4s ease-in-out infinite}@keyframes ready{0%,100%{opacity:1}50%{opacity:.35}}}
@media (prefers-reduced-motion:no-preference){.lv-now::before{animation:pulse 2.4s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--gold) 55%,transparent)}100%{box-shadow:0 0 0 9px transparent}}}
/* ---- 2026 refresh: steel + gold, collapsible panels ---- */
:root{--bg:#e8ebef;--paper:#f8f9fb;--raise:#ffffff;--ink:#141a21;--soft:#5a6472;--line:#d5dbe3;--gold:#a7802a;--gold-2:#ecdcae;
--up:#1d8656;--down:#c0402c;--up-bg:#e1f2e8;--down-bg:#f8e4de;--news:#d9b14f;--shadow:0 1px 2px rgba(20,26,33,.06),0 8px 24px -12px rgba(20,26,33,.18)}
@media (prefers-color-scheme:dark){:root{--bg:#0e1217;--paper:#161b22;--raise:#1c222b;--ink:#e6e9ee;--soft:#8c96a5;--line:#262e39;--gold:#d8b45c;--gold-2:#3d3420;
--up:#4fc489;--down:#f0775f;--up-bg:#14301f;--down-bg:#3a1d17;--news:#b18f3a;--shadow:0 1px 0 rgba(255,255,255,.03),0 10px 30px -14px rgba(0,0,0,.7)}}
body{font-size:15px;line-height:1.5}
.wrap{max-width:1100px;padding:0 20px 56px}
.top{position:sticky;top:0;z-index:20;margin:0 -20px 18px;padding:14px 20px;background:color-mix(in srgb,var(--bg) 84%,transparent);
  -webkit-backdrop-filter:saturate(1.4) blur(12px);backdrop-filter:saturate(1.4) blur(12px);border-bottom:1px solid color-mix(in srgb,var(--line) 70%,transparent);align-items:center}
.top h1{font-size:22px;font-stretch:125%;letter-spacing:-.015em;display:flex;align-items:center;gap:10px}
.top h1::before{content:"";width:14px;height:22px;border-radius:3px;background:linear-gradient(160deg,#f3dc95,var(--gold) 55%,#7a5a17);box-shadow:inset 0 0 0 1px rgba(0,0,0,.15)}
.status>span{background:var(--paper);border:1px solid var(--line);border-radius:999px;padding:3px 10px}
.acts{gap:10px;margin-bottom:14px}
.act{border-radius:18px;box-shadow:var(--shadow);background:var(--raise)}
.quiet{border-radius:16px;background:var(--raise);box-shadow:var(--shadow);padding:10px 14px;gap:8px}
.q-word{text-transform:none;letter-spacing:0;font-size:14px;color:var(--ink);font-weight:700;font-stretch:112%}
.chip{background:var(--paper);font-size:13px}.chip b{margin-right:2px}
.chip.on{border-color:var(--gold);background:color-mix(in srgb,var(--gold) 12%,var(--paper));color:var(--ink)}
.chip.on::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--gold);margin-right:7px;vertical-align:1px}
.newsbar{border-radius:14px;border-left-width:4px;background:var(--raise);box-shadow:var(--shadow);margin-bottom:14px}
details.watch{display:block;background:var(--raise);border:1px solid var(--line);border-radius:20px;margin-bottom:14px;padding:0;box-shadow:var(--shadow);overflow:hidden}
details.watch.on{border-color:color-mix(in srgb,var(--gold) 55%,var(--line))}
details.watch.long{border-color:color-mix(in srgb,var(--up) 50%,var(--line))}details.watch.short{border-color:color-mix(in srgb,var(--down) 50%,var(--line))}
details.watch>summary{list-style:none;display:grid;grid-template-columns:minmax(0,auto) minmax(0,1fr) auto 22px;align-items:center;gap:14px;
  padding:16px 22px;margin:0;cursor:pointer;color:var(--ink);font-size:15px}
details.watch>summary::-webkit-details-marker{display:none}
details.watch>summary:hover{background:color-mix(in srgb,var(--paper) 60%,transparent)}
details.watch>summary:focus-visible{outline:2px solid var(--gold);outline-offset:-4px;border-radius:20px}
.w-name{font:700 20px/1.1 "Archivo",sans-serif;font-stretch:118%;white-space:nowrap}
.w-state{justify-self:start;font-size:13px;color:var(--soft);border:1px solid var(--line);border-radius:999px;padding:3px 11px;background:var(--paper);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.w-state.on{color:var(--ink);border-color:var(--gold);background:color-mix(in srgb,var(--gold) 12%,var(--paper))}
.w-state.long{color:var(--up);border-color:var(--up);background:var(--up-bg)}.w-state.short{color:var(--down);border-color:var(--down);background:var(--down-bg)}
.w-px{font:650 20px/1 "Archivo",sans-serif;font-stretch:125%;letter-spacing:-.01em}
.chev{width:10px;height:10px;border-right:2px solid var(--soft);border-bottom:2px solid var(--soft);transform:rotate(45deg) translate(-2px,-2px);transition:transform .2s ease;justify-self:center}
details.watch[open]>summary .chev{transform:rotate(225deg) translate(-2px,-2px)}
details.watch[open]>summary{border-bottom:1px solid var(--line)}
.w-body{padding:16px 22px 20px}
.w-meta{margin:0 0 14px;color:var(--soft);font-size:13px}
.pos{border-radius:16px;background:var(--paper)}
.pos.flat .price{font-stretch:125%;font-size:46px;background:linear-gradient(180deg,var(--ink) 55%,color-mix(in srgb,var(--ink) 55%,var(--gold)));-webkit-background-clip:text;background-clip:text;color:transparent}
.tl-track{height:14px;border-radius:999px}.tl-win{border-radius:999px}
.trades{border-top:1px solid var(--line)}
.tt td,.tt th{padding-left:10px;padding-right:10px}
.tt tbody tr:hover{background:color-mix(in srgb,var(--paper) 70%,transparent)}
.panel{background:var(--raise);border-radius:20px;box-shadow:var(--shadow)}
details.earlier>summary{cursor:pointer;color:var(--soft);font-size:14px;margin:10px 0 0;font-weight:600}
details.more>summary,details.earlier>summary{list-style:none}
details.more>summary::before,details.earlier>summary::before{content:"";display:inline-block;width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);margin-right:9px;vertical-align:2px;transition:transform .2s ease}
details.more[open]>summary::before,details.earlier[open]>summary::before{transform:rotate(45deg)}
details.more>summary::-webkit-details-marker,details.earlier>summary::-webkit-details-marker{display:none}
.sigbtn,.ready-badge{box-shadow:var(--shadow)}
.foot{text-align:center}
@media (max-width:560px){details.watch>summary{grid-template-columns:minmax(0,1fr) auto 18px;gap:8px 10px;padding:14px 16px}
  .w-state{grid-column:1/-1;grid-row:2;justify-self:start}.w-name{font-size:18px}.w-px{font-size:17px}.w-body{padding:14px 16px 18px}
  .top{flex-direction:column;align-items:flex-start;gap:8px}}
.al details>summary{list-style:none}.al details>summary::-webkit-details-marker{display:none}.al details>summary::before{content:"";display:inline-block;width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);margin-right:9px;vertical-align:2px;transition:transform .2s ease}.al details[open]>summary::before{transform:rotate(45deg)}
@media (max-width:560px){.status>span:last-child{display:none}.top h1{font-size:19px}}
@media (prefers-reduced-motion:reduce){.al details>summary::before,.chev,details.more>summary::before,details.earlier>summary::before{transition:none}}
"""


FRESH_MIN = 30  # a new entry signal flashes at the top of the page for this many minutes


def _signal_button(lv) -> str:
    """Blinking BUY / SELL button in the watch panel while a new signal is fresh; HOLD while in a trade."""
    t = lv.res.open_trade if lv.res else None
    if not t:
        return ""
    fired = t.signal_time + lv.book.bar_ms
    buy = t.direction > 0
    side = "BUY" if buy else "SELL"
    mins = max(0, int((time.time() * 1000 - fired) // 60_000))
    levels = f"Entry {_px(t.signal_close)} · SL {_px(t.stop)} · TP {_px(t.target)}"
    if mins <= FRESH_MIN:
        ago = "just now" if mins < 2 else f"{mins} min ago"
        return (f'<div class="sigbtn flash {side.lower()}" role="alert"><span class="sb-word">{side} NOW</span>'
                f'<span class="sb-lv">{levels}</span><span class="sb-sm">signal {_clock(fired)} ({ago}) · don\'t enter if price ran far past entry</span></div>')
    return (f'<div class="sigbtn hold {side.lower()}"><span class="sb-word">HOLD {"LONG" if buy else "SHORT"}</span>'
            f'<span class="sb-lv">{levels}</span><span class="sb-sm">only if you took it · no new entry until it closes</span></div>')


def _in_window(lv, now: datetime) -> bool | None:
    p = lv.strategy.p
    a = getattr(p, "session_start", getattr(p, "from_hhmm", None))
    b = getattr(p, "session_end", getattr(p, "to_hhmm", None))
    if a is None or b is None:
        return None
    hm = now.hour * 100 + now.minute
    if a > b:  # overnight window
        return (hm >= a and now.weekday() in (6, 0, 1, 2, 3)) or (hm < b and now.weekday() in (0, 1, 2, 3, 4))
    return now.weekday() < 5 and a <= hm < b


def _action(lv) -> str:
    """One big word per watch: BUY / SELL (flashing, fresh signal), HOLD (trade open),
    EXIT (trade just closed), NO ACTION (flat)."""
    now_ms = time.time() * 1000
    now = datetime.now(CHICAGO)
    res = lv.res
    name = f"{E(lv.watch.name.title())} {E(lv.symbol)}{' (paper)' if lv.watch.paper else ''}"
    names = " or ".join(contract_names(lv.watch, lv.symbol)) or lv.symbol
    t = res.open_trade if res else None
    if t:
        fired = t.signal_time + lv.book.bar_ms  # the alert goes out when the signal bar closes
        buy = t.direction > 0
        below, above = ("below", "above") if buy else ("above", "below")
        mins = max(0, int((now_ms - fired) // 60_000))
        if mins <= FRESH_MIN:
            return f"""<div class="act flash {'buy' if buy else 'sell'}" role="alert">
  <p class="act-word">{'BUY' if buy else 'SELL'}</p>
  <div class="act-body"><p class="act-head">{name}, signal {_clock(fired)} ({'just now' if mins < 2 else f'{mins} min ago'})</p>
  <p>On Robinhood: {'buy' if buy else 'sell'} {E(names)}, then place a stop <b>{t.risk:.2f} pts {below}</b> and a limit <b>{t.reward:.2f} pts {above}</b> your fill.</p>
  <p class="act-sm">Signal close {_px(t.signal_close)}, stop {_px(t.stop)}, target {_px(t.target)}</p></div></div>"""
        return f"""<div class="act hold {'buy' if buy else 'sell'}">
  <p class="act-word">HOLD</p>
  <div class="act-body"><p class="act-head">{name}: {'long' if buy else 'short'} since {_clock(fired)}{'' if _day(fired) == 'Today' else ' ' + _day(fired)}</p>
  <p>If you took this trade: keep your stop at {_px(t.stop)} and limit at {_px(t.target)} (or the same distances from your fill). If you didn't, do nothing: don't enter late. No new signals until it closes.</p></div></div>"""
    last = res.trades[-1] if res and res.trades else None
    if last and last.exit_time and now_ms - (last.exit_time + lv.book.bar_ms) <= FRESH_MIN * 60_000:
        won = (last.points or 0) > 0
        why = {"target": "target hit", "stop": "stop hit", "time": "end-of-day exit"}.get(last.exit_reason or "", "closed")
        return f"""<div class="act exit {'win' if won else 'loss'}">
  <p class="act-word">EXIT</p>
  <div class="act-body"><p class="act-head">{name}: {why}, {last.points:+.2f} pts</p>
  <p>{'Close the position now on Robinhood.' if last.exit_reason == 'time' else 'Your stop or limit should have filled. Check Robinhood and cancel the other order.'}</p></div></div>"""
    inwin = _in_window(lv, now)
    sub = "watching now" if inwin else _window(lv).replace(' and ', '–').replace(' CT', '')
    return f'<span class="chip{" on" if inwin else ""}"><b>{E(lv.watch.name.title())}</b> {sub}</span>'


def render(lives, nd: dict[date, str], alerts: list[dict], started: float, mkt_open: bool) -> str:
    now_ct = datetime.now(CHICAGO)
    today = now_ct.date()
    past_news = now_ct.hour >= 8  # today's 7:30 release and the 7:00-8:00 block are over
    upcoming = sorted((d, n) for d, n in nd.items() if d > today or (d == today and not past_news))[:5]
    news_today = None if past_news else nd.get(today)
    if news_today:
        newsbar = f'<p class="newsbar"><b>{E(news_today)} today at 7:30 CT.</b> Gold takes no new entries from 7:00 to 8:00.</p>'
    elif upcoming:
        nxt = ", ".join(f"{E(n)} {d:%a %b %-d}" for d, n in upcoming[:3])
        newsbar = f'<p class="newsbar">Next news days: {nxt}. Gold skips 7:00–8:00 CT on those mornings.</p>'
    else:
        newsbar = ""
    watches = "".join(_watch(lv, nd) for lv in lives)
    acts = [_action(lv) for lv in lives if lv.res]
    big = [a for a in acts if not a.startswith('<span class="chip')]
    chips = [a for a in acts if a.startswith('<span class="chip')]
    quiet = (f'<div class="quiet"><span class="q-word">No action now</span>{"".join(chips)}</div>' if chips else "")
    flash_html = f'<div class="acts">{"".join(big)}{quiet}</div>'
    title = "Signal Desk"
    for lv, a in zip([lv for lv in lives if lv.res], acts):
        if 'class="act flash' in a:
            title = f"{'BUY' if lv.res.open_trade.direction > 0 else 'SELL'} {lv.symbol} · Signal Desk"
            break
    now = time.time()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<noscript><meta http-equiv="refresh" content="15"></noscript><title>{E(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">
<header class="top"><h1>Signal Desk</h1>
<p class="status"><span><span class="dot{' on' if mkt_open else ''}"></span>Market {'open' if mkt_open else 'closed'}</span>
<span id="upd" data-t="{int(now)}">Updated {_clock(now)} CT</span><span>Running since {_clock(started)}{'' if _day(started) == 'Today' else ', ' + _day(started)}</span></p></header>
{flash_html}{newsbar}{watches}
<section class="panel"><h2>Alerts sent</h2>{_alerts(alerts)}</section>
<p class="foot">Signals only, not trading advice. Trades are placed by you on Robinhood. This page reloads every 15 seconds while the bot runs.</p>
</div>
<script>
(function(){{var e=document.getElementById('upd');var t=+e.dataset.t;setInterval(function(){{var m=Math.floor((Date.now()/1000-t)/60);
if(m>=3){{e.textContent='Not updated for '+m+' min: the bot may have stopped';e.className='stale';}}}},20000);
if(document.querySelector('.act.flash')){{var base=document.title,on=false;setInterval(function(){{on=!on;document.title=on?'🔔 '+base:base;}},900);}}function g(k){{try{{return localStorage.getItem(k)}}catch(x){{return null}}}}function st(k,v){{try{{localStorage.setItem(k,v)}}catch(x){{}}}}document.querySelectorAll('details[data-key]').forEach(function(d){{var k='sd:'+d.dataset.key,v=g(k);if(d.dataset.force==='1'){{d.open=true}}else if(v!==null){{d.open=v==='1'}}d.addEventListener('toggle',function(){{st(k,d.open?'1':'0')}})}});try{{var y=sessionStorage.getItem('sd:y');if(y!==null)window.scrollTo(0,+y)}}catch(x){{}}setTimeout(function(){{try{{sessionStorage.setItem('sd:y',String(window.scrollY))}}catch(x){{}}location.reload()}},15000);}})();
</script></body></html>"""


def write(path: Path, lives, nd, alerts, started, mkt_open) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render(lives, nd, alerts, started, mkt_open), encoding="utf-8")
    os.replace(tmp, path)
