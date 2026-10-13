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
  <p class="pos-sub">No open trade on {E(lv.symbol)}. Signals can fire between {_window(lv)}.</p>
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
    if a > b:  # overnight window (e.g. Asia): the morning strip doesn't apply
        return f'<p class="tl-cap">Entry window {_window(lv)} (overnight)</p>'
    start, end = 6 * 60, 12 * 60

    def x(hhmm: int) -> float:
        m = hhmm // 100 * 60 + hhmm % 100
        return max(0.0, min(100.0, (m - start) / (end - start) * 100))

    now = datetime.now(CHICAGO)
    now_m = now.hour * 100 + now.minute
    marker = f'<div class="tl-now" style="left:{x(now_m):.1f}%"><span>now</span></div>' if 600 <= now_m < 1200 and now.weekday() < 5 else ""
    news = (f'<div class="tl-news" style="left:{x(700):.1f}%;width:{x(800) - x(700):.1f}%" title="{E(news_today)} at 7:30, no entries"></div>'
            if news_today and lv.watch.news_alert else "")
    ticks = "".join(f'<span style="left:{x(h * 100):.1f}%">{h if h <= 12 else h - 12}{"am" if h < 12 else "pm"}</span>' for h in range(6, 13))
    trades = "".join(
        f'<div class="tl-trade {"long" if t.direction > 0 else "short"}" style="left:{x(_dt(t.signal_time).hour * 100 + _dt(t.signal_time).minute):.1f}%" title="{"Long" if t.direction > 0 else "Short"} signal {_clock(t.signal_time + lv.book.bar_ms)}"></div>'
        for t in lv.res.trades if _dt(t.signal_time).date() == now.date()
    )
    return f"""<div class="tl">
  <div class="tl-track"><div class="tl-win" style="left:{x(a):.1f}%;width:{x(b) - x(a):.1f}%"></div>{news}{trades}{marker}</div>
  <div class="tl-ticks">{ticks}</div>
  <p class="tl-cap">Entry window {_window(lv)}{f'. {E(news_today)} at 7:30: no entries 7–8' if news_today and lv.watch.news_alert else ''}</p>
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
    rows = "".join(
        f'<tr><td>{_day(t.signal_time)}<small>{_clock(t.signal_time + lv.book.bar_ms)}</small></td>'
        f'<td><span class="tag {"long" if t.direction > 0 else "short"}">{"Long" if t.direction > 0 else "Short"}</span></td>'
        f'<td class="num">{_px(t.entry_price or t.signal_close)}</td><td class="num">{_px(t.exit_price)}</td>'
        f'<td>{E({"target": "Target", "stop": "Stop", "time": "Time exit"}.get(t.exit_reason or "", "Open"))}</td>'
        f'<td class="num {"up" if (t.points or 0) > 0 else "down" if t.points is not None else ""}">{"" if t.points is None else f"{t.points:+.2f}"}</td></tr>'
        for t in reversed(res.trades[-12:])
    )
    return f"""<h3>Last {days} days</h3>{summary}
<table class="tt"><thead><tr><th>Signal</th><th>Side</th><th class="num">Entry</th><th class="num">Exit</th><th>Result</th><th class="num">Pts</th></tr></thead><tbody>{rows}</tbody></table>"""


def _watch(lv, nd) -> str:
    w = lv.watch
    bars = lv.book.closed_bars()
    if lv.res is None or not bars:
        return f'<section class="watch"><h2>{E(w.name.title())}</h2><p class="muted">Waiting for market data.</p></section>'
    last = bars[-1]
    news_today = nd.get(datetime.now(CHICAGO).date())
    return f"""<section class="watch">
  <header class="wh"><h2>{E(w.name.title())}{' <span class="paper">paper</span>' if w.paper else ''}</h2><p>{E(lv.symbol)} on {E(w.interval)} bars, strategy {E(w.strategy.replace('_', ' '))}</p></header>
  <div class="cols">
    <div class="col-a">{_position(lv, last)}{_timeline(lv, news_today)}</div>
    <div class="col-b"><h3>Ready to enter?</h3>{_checks(lv, nd)}</div>
  </div>
  <div class="trades">{_trades(lv)}</div>
</section>"""


def _alerts(alerts: list[dict]) -> str:
    if not alerts:
        return '<p class="muted">Nothing sent yet. Trade signals, exits, news-day warnings and the 7:15 check-in will show here.</p>'
    items = []
    for a in reversed(alerts[-25:]):
        body = E(a["body"]).replace("\n", "<br>")
        items.append(
            f'<li class="al {E(a.get("kind", "info"))}"><time>{_day(a["t"])}<br>{_clock(a["t"])}</time>'
            f'<details{" open" if a.get("kind") in ("entry", "exit") else ""}><summary>{E(a["title"])}</summary><p>{body}</p></details></li>'
        )
    return f'<ol class="alerts">{"".join(items)}</ol>'


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
.trades{margin-top:22px;border-top:1px solid var(--line);padding-top:16px}
.tsum{display:flex;flex-wrap:wrap;gap:8px 28px;margin-bottom:10px}
.tsum div{display:flex;flex-direction:column}.tsum b{font:600 22px/1.1 "Archivo",sans-serif;font-stretch:112%}.tsum span{font-size:12px;color:var(--soft)}
.tt{width:100%;border-collapse:collapse;font-size:14px}
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
@media (prefers-reduced-motion:no-preference){.lv-now::before{animation:pulse 2.4s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--gold) 55%,transparent)}100%{box-shadow:0 0 0 9px transparent}}}
"""


FRESH_MIN = 30  # a new entry signal flashes at the top of the page for this many minutes


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
    if inwin:
        sub = f"Watching for a setup until {_window(lv).split(' and ')[1]}. Nothing to do yet."
    else:
        sub = f"Flat. Next chance between {_window(lv)} on the next trading day." if inwin is False else "Flat. Nothing to do."
    return f"""<div class="act none"><p class="act-word">NO ACTION</p>
  <div class="act-body"><p class="act-head">{name}</p><p>{sub}</p></div></div>"""


def render(lives, nd: dict[date, str], alerts: list[dict], started: float, mkt_open: bool) -> str:
    today = datetime.now(CHICAGO).date()
    upcoming = sorted((d, n) for d, n in nd.items() if d >= today)[:5]
    news_today = nd.get(today)
    if news_today:
        newsbar = f'<p class="newsbar"><b>{E(news_today)} today at 7:30 CT.</b> Gold takes no new entries from 7:00 to 8:00.</p>'
    elif upcoming:
        nxt = ", ".join(f"{E(n)} {d:%a %b %-d}" for d, n in upcoming[:3])
        newsbar = f'<p class="newsbar">Next news days: {nxt}. Gold skips 7:00–8:00 CT on those mornings.</p>'
    else:
        newsbar = ""
    watches = "".join(_watch(lv, nd) for lv in lives)
    acts = [_action(lv) for lv in lives if lv.res]
    flash_html = f'<div class="acts">{"".join(acts)}</div>'
    title = "Signal Desk"
    for lv, a in zip([lv for lv in lives if lv.res], acts):
        if 'class="act flash' in a:
            title = f"{'BUY' if lv.res.open_trade.direction > 0 else 'SELL'} {lv.symbol} · Signal Desk"
            break
    now = time.time()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15"><title>{E(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">
<header class="top"><h1>Signal <em>Desk</em></h1>
<p class="status"><span><span class="dot{' on' if mkt_open else ''}"></span>Market {'open' if mkt_open else 'closed'}</span>
<span id="upd" data-t="{int(now)}">Updated {_clock(now)} CT</span><span>Running since {_clock(started)}{'' if _day(started) == 'Today' else ', ' + _day(started)}</span></p></header>
{flash_html}{newsbar}{watches}
<section class="panel"><h2>Alerts sent</h2>{_alerts(alerts)}</section>
<p class="foot">Signals only, not trading advice. Trades are placed by you on Robinhood. This page reloads every 15 seconds while the bot runs.</p>
</div>
<script>
(function(){{var e=document.getElementById('upd');var t=+e.dataset.t;setInterval(function(){{var m=Math.floor((Date.now()/1000-t)/60);
if(m>=3){{e.textContent='Not updated for '+m+' min: the bot may have stopped';e.className='stale';}}}},20000);
if(document.querySelector('.act.flash')){{var base=document.title,on=false;setInterval(function(){{on=!on;document.title=on?'🔔 '+base:base;}},900);}}}})();
</script></body></html>"""


def write(path: Path, lives, nd, alerts, started, mkt_open) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render(lives, nd, alerts, started, mkt_open), encoding="utf-8")
    os.replace(tmp, path)
