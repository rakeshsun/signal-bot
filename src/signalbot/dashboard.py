"""A self-contained HTML status page, rewritten every minute by the live bot.

Best viewed at http://localhost:8765/ (served by the bot: live updates, sound, journal, phone
access). logs/dashboard.html still works opened as a file (it reloads itself every 15 s).
Layout: sticky top bar (instrument price), strategy rail, one "now" card (BUY / SELL / HOLD /
EXIT or no action), then a folding panel per strategy (position, session timeline, next step,
recent trades) and the activity feed. Dark by default with a remembered light/dark switch.
"""

from __future__ import annotations

import html
import json
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
    stop = getattr(t, "active_stop", t.stop)  # runner: the stop sits at entry once the target took part off
    pts = {"Target": t.target, "Entry": entry, "Now": last_close, "Stop": stop}
    if stop == entry:
        pts.pop("Stop")
    hi, lo = max(pts.values()), min(pts.values())
    span = (hi - lo) or 1.0

    def y(v: float) -> float:
        return 6 + (hi - v) / span * 88  # % from top

    zone_good = (min(entry, t.target), max(entry, t.target))
    zone_bad = (min(entry, stop), max(entry, stop))
    # label positions, pushed apart (top to bottom) so close prices never print on top of each other
    pos, last = {}, None
    for k, v in sorted(pts.items(), key=lambda kv: y(kv[1])):
        top = y(v) if last is None else max(y(v), last + 11)
        pos[k] = last = top
    over = max(0.0, last - 96)  # shift the stack up if it ran off the bottom
    rows = "".join(
        f'<div class="lv lv-{k.lower()}" style="top:{pos[k] - over:.1f}%"><span class="lv-k">{k}</span><span class="lv-v">{_px(v)}</span></div>'
        for k, v in pts.items()
    )
    return f"""<div class="ladder" role="img" aria-label="Stop {_px(stop)}, entry {_px(entry)}, now {_px(last_close)}, target {_px(t.target)}">
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
        prev = lv.res.trades[-1] if lv.res.trades else None
        prev_txt = ""
        if prev and prev.points is not None:
            prev_txt = (f'<p class="pos-prev">Last trade: {"long" if prev.direction > 0 else "short"} {_day(prev.signal_time).lower() if _day(prev.signal_time) == "Today" else _day(prev.signal_time)} '
                        f'{_clock(prev.signal_time + lv.book.bar_ms)}, <b class="{"up" if prev.points > 0 else "down"}">{prev.points:+.2f} pts</b> ({E(prev.exit_reason or "")})</p>')
        return f"""<div class="pos flat">
  <p class="pos-state">Flat</p>
  <p class="pos-sub">No open trade.</p>{prev_txt}
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
    {_runner_note(t) if t.runner else f'<p class="pos-sub">Stop {t.risk:.2f} pts away, target {t.reward:.2f} pts away</p>'}
  </div>
  {_ladder(t, now_px)}
</div>"""


def _runner_note(t) -> str:
    entry_px = t.entry_price or t.signal_close
    if t.part_price is None:
        return (f'<p class="pos-sub">Runner trade: {1 - t.runner:.0%} closes at target {_px(t.target)}, '
                f'{t.runner:.0%} rides. Stop {_px(t.stop)} for all until then.</p>')
    first = (t.part_price - entry_px) * t.direction
    sw = f", trend breaks below {_px(t.swing)}" if t.swing is not None and t.direction > 0 else (
         f", trend breaks above {_px(t.swing)}" if t.swing is not None else "")
    return (f'<p class="pos-sub"><b class="up">{1 - t.runner:.0%} closed +{first:.2f} pts</b> at {_px(t.part_price)}. '
            f'Runner {t.runner:.0%}: stop at entry {_px(entry_px)}{sw}.</p>')


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
    span = ((b // 100 * 60 + b % 100) - (a // 100 * 60 + a % 100)) % 1440
    allday = night and span > 12 * 60  # e.g. 5 PM - 4 PM: show the whole CME day
    start, end = ((17 * 60, 41 * 60) if allday else (16 * 60, 28 * 60)) if night else (6 * 60, 12 * 60)

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
    hours = (range(17, 42, 4) if allday else range(16, 29, 2)) if night else range(6, 13)
    ticks = "".join(f'<span style="left:{(h * 60 - start) / (end - start) * 100:.1f}%">{(h % 12) or 12}{"am" if h % 24 < 12 else "pm"}</span>' for h in hours)
    recent = now.timestamp() * 1000 - (24 if allday else 14) * 3600_000
    trades = "".join(
        f'<div class="tl-trade {"long" if t.direction > 0 else "short"}" style="left:{x(_dt(t.signal_time).hour * 100 + _dt(t.signal_time).minute):.1f}%" title="{"Long" if t.direction > 0 else "Short"} signal {_clock(t.signal_time + lv.book.bar_ms)}"></div>'
        for t in lv.res.trades if (t.signal_time >= recent if night else _dt(t.signal_time).date() == now.date())
    )
    if night:
        news_today = None
    return f"""<div class="tl">
  <div class="tl-track"><div class="tl-win" style="left:{x(a):.1f}%;width:{x(b) - x(a):.1f}%"></div>{news}{trades}{marker}</div>
  <div class="tl-ticks">{ticks}</div>
  <p class="tl-cap">Entry window {_window(lv)}{' (all day, Sun–Fri)' if allday else ' (overnight)' if night else ''}{f'. {E(news_today)} at 7:30: no entries 7–8' if news_today and lv.watch.news_alert else ''}</p>
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
    exit_btn = 'exitf' in sig and not (lv.res and lv.res.open_trade)
    if sig:
        out.append(sig)
        if exit_btn:
            sig = ""  # after an exit, still show what comes next
    if sig:
        pass
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


def _stats(lv):
    closed = [t for t in lv.res.trades if not t.is_open]
    wins = sum(1 for t in closed if t.won)
    gw = sum(t.points for t in closed if (t.points or 0) > 0)
    gl = -sum(t.points for t in closed if (t.points or 0) <= 0)
    names = contract_names(lv.watch, lv.symbol)
    first = next(iter(names.items()), None)
    days = max(1, len(lv.res.bars) * lv.book.bar_ms // 86_400_000)
    return {"n": len(closed), "wins": wins, "pf": (gw / gl) if gl else 0.0, "net": gw - gl,
            "usd": _usd((gw - gl) * first[1]) if first else "", "unit": first[0] if first else "", "days": days}


def _state(lv, now_ct):
    """(label, css class, is_open, force_open) for the panel header and the rail."""
    t = lv.res.open_trade if lv.res else None
    inwin = _in_window(lv, now_ct)
    now_ms = time.time() * 1000
    last_t = lv.res.trades[-1] if lv.res and lv.res.trades else None
    fresh_exit = bool(last_t and last_t.exit_time and now_ms - (last_t.exit_time + lv.book.bar_ms) <= FRESH_MIN * 60_000)
    if t:
        fresh = now_ms - (t.signal_time + lv.book.bar_ms) <= FRESH_MIN * 60_000
        side = "Buy" if t.direction > 0 else "Sell"
        label = f"{side} signal {_clock(t.signal_time + lv.book.bar_ms)}" if fresh else f"{'Long' if t.direction > 0 else 'Short'} since {_clock(t.signal_time + lv.book.bar_ms)}"
        return label, ("long" if t.direction > 0 else "short"), True, fresh
    if fresh_exit:
        return f"Closed {'+' if (last_t.points or 0) > 0 else ''}{last_t.points:.1f} pts", "exit", True, False
    if inwin:
        return f"Watching until {_window(lv).split(' and ')[1].replace(' CT', '')}", "on", True, False
    return f"Opens {_window(lv).split(' and ')[0]}", "off", False, False


def _opens_in(lv, now_ct) -> str:
    t = _next_open_ms(lv, now_ct)
    return f' · in <span class="cd" data-t="{t}">–</span>' if t else ""


def _watch(lv, nd) -> str:
    w = lv.watch
    bars = lv.book.closed_bars()
    slug = "w-" + "".join(c if c.isalnum() else "-" for c in w.name.lower())
    if lv.res is None or not bars:
        return f'<section class="watch" id="{slug}"><div class="wh"><span class="w-name">{E(w.name.title())}</span><span class="w-state off">Waiting for market data</span></div></section>'
    last = bars[-1]
    now_ct = datetime.now(CHICAGO)
    news_today = nd.get(now_ct.date()) if now_ct.hour < 8 else None  # the 7:00-8:00 block is over after 8
    label, cls, is_open, force = _state(lv, now_ct)
    st = _stats(lv)
    key = f"w:{w.name}:{'open' if is_open else 'shut'}"
    pf_cls = "up" if st["pf"] >= 1 else "down"
    return f"""<details class="watch {cls}" id="{slug}" data-key="{E(key)}"{' data-force="1"' if force else ''}{' open' if is_open else ''}>
  <summary class="wh">
    <span class="w-id"><span class="w-name">{E(w.name.title())}</span>{'<span class="paper">Paper</span>' if w.paper else '<span class="live-tag">Live</span>'}</span>
    <span class="w-state {cls}">{E(label)}{_opens_in(lv, now_ct) if cls == "off" else ""}</span>
    <span class="w-stats" title="Last {st['days']} days in the loaded history">
      <span><b>{st['wins']}/{st['n']}</b> won</span><span><b class="{pf_cls}">{st['pf']:.2f}</b> PF</span><span><b class="{'up' if st['net'] >= 0 else 'down'}">{st['usd']}</b> per {E(st['unit'])}</span>
    </span>
    <span class="chev" aria-hidden="true"></span>
  </summary>
  <div class="w-body">
    {_chart(lv, bars, _live(lv)[1])}
    <div class="cols">
      <div class="col-a"><h3>Position</h3>{_position(lv, last)}{_timeline(lv, news_today)}</div>
      <div class="col-b"><h3>Next step</h3>{_checks(lv, nd)}</div>
    </div>
    {_recent(lv)}
    <p class="w-meta">{E(lv.symbol)} on {E(w.interval)} bars, strategy {E(w.strategy.replace('_', ' '))}. Stats cover the last {st['days']} days.</p>
  </div>
</details>"""


def _recent(lv) -> str:
    trades = list(reversed(lv.res.trades[-10:]))
    if not trades:
        return ""
    rows = "".join(
        f'<tr><td>{_day(t.signal_time)}<small>{_clock(t.signal_time + lv.book.bar_ms)}</small></td>'
        f'<td><span class="tag {"long" if t.direction > 0 else "short"}">{"Long" if t.direction > 0 else "Short"}</span></td>'
        f'<td class="num">{_px(t.entry_price or t.signal_close)}</td><td class="num">{_px(t.exit_price)}</td>'
        f'<td>{E(_result(t))}</td>'
        f'<td class="num {"up" if (t.points or 0) > 0 else "down" if t.points is not None else ""}">{"" if t.points is None else f"{t.points:+.2f}"}</td>'
        f'<td>{_jr(lv, t)}</td></tr>'
        for t in trades)
    return (f'<details class="more trades" data-key="t:{E(lv.watch.name)}"><summary>Recent trades ({len(trades)})</summary>'
            '<table class="tt"><thead><tr><th>Signal</th><th>Side</th><th class="num">Entry</th><th class="num">Exit</th><th>Result</th><th class="num">Pts</th><th>You</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></details>')


def _result(t) -> str:
    if t.exit_reason is None:
        return "Half off, riding" if t.part_price is not None else "Open"
    if t.part_price is not None:
        return {"choch": "Target + trend exit", "stop": "Target + stop at entry", "time": "Target + time exit"}.get(t.exit_reason, "Closed")
    return {"target": "Target", "stop": "Stop", "time": "Time exit"}.get(t.exit_reason, "Closed")


def _jr(lv, t) -> str:
    k = E(f"{lv.watch.name}|{t.signal_time}")
    return (f'<span class="jr" data-k="{k}"><button type="button" data-c="took">Took it</button>'
            f'<button type="button" data-c="skipped">Skipped</button></span>')


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
:root,[data-theme=dark]{color-scheme:dark;--bg:#0f1318;--surface:#151b22;--raise:#1a2129;--hover:#1f2831;--line:#252e39;--line-2:#323d4a;
--ink:#e8edf2;--soft:#93a0b1;--faint:#647184;--gold:#d6b25e;--gold-2:#3b3220;--gold-ink:#f0d792;
--up:#3fbf83;--down:#ef6b54;--exit:#c27c0e;--up-bg:rgba(63,191,131,.13);--down-bg:rgba(239,107,84,.13);--news:#c9a24a;
--shadow:0 1px 0 rgba(255,255,255,.025) inset,0 12px 32px -18px rgba(0,0,0,.8)}
[data-theme=light]{color-scheme:light;--bg:#eef1f5;--surface:#ffffff;--raise:#ffffff;--hover:#f5f7fa;--line:#e1e6ec;--line-2:#cfd6df;
--ink:#121821;--soft:#5a6677;--faint:#8a95a5;--gold:#9a7420;--gold-2:#f3e7c4;--gold-ink:#7a5a12;
--up:#147a4a;--down:#c23a25;--exit:#b86e00;--up-bg:#e3f3ea;--down-bg:#fbe7e2;--news:#c99a2e;
--shadow:0 1px 2px rgba(18,24,33,.05),0 10px 28px -16px rgba(18,24,33,.22)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;scroll-padding-top:76px}
body{margin:0;background:var(--bg);color:var(--ink);font:14.5px/1.5 "Archivo",system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}
a{color:inherit}
h2,h3,h4{margin:0}
.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--soft)}.small{font-size:12px}
.num{text-align:right!important}

/* top bar */
.top{position:sticky;top:0;z-index:30;background:color-mix(in srgb,var(--bg) 82%,transparent);-webkit-backdrop-filter:saturate(1.5) blur(14px);backdrop-filter:saturate(1.5) blur(14px);border-bottom:1px solid var(--line)}
.top-in{max-width:1320px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:28px}
.brand{margin:0;display:flex;align-items:center;gap:10px;font:800 17px/1 "Archivo",sans-serif;font-stretch:125%;letter-spacing:-.01em;white-space:nowrap}
.ingot{width:12px;height:20px;border-radius:2px;background:linear-gradient(155deg,#f6e2a2,var(--gold) 50%,#6e5016);box-shadow:inset 0 0 0 1px rgba(0,0,0,.2)}
.tick{display:flex;align-items:baseline;gap:10px;padding-left:24px;border-left:1px solid var(--line)}
.tk-sym{color:var(--soft);font-weight:600;font-size:13px}
.tk-px{font:700 24px/1 "Archivo",sans-serif;font-stretch:125%;letter-spacing:-.02em}
.tk-chg{font-weight:700;font-size:14px}.tk-note{color:var(--faint);font-size:12px}
.status{margin:0 0 0 auto;display:flex;align-items:center;gap:14px;color:var(--soft);font-size:13px;white-space:nowrap}
.mk{display:inline-flex;align-items:center;gap:7px}.mk .dot{width:8px;height:8px;border-radius:50%;background:var(--faint)}
.mk.on{color:var(--ink)}.mk.on .dot{background:var(--up);box-shadow:0 0 0 3px var(--up-bg)}
.stale{color:var(--down);font-weight:700}
#theme{font:inherit;font-size:12px;color:var(--soft);background:transparent;border:1px solid var(--line-2);border-radius:8px;padding:5px 10px;cursor:pointer}
#theme:hover{color:var(--ink);border-color:var(--soft)}
#theme:focus-visible,.ri:focus-visible,summary:focus-visible{outline:2px solid var(--gold);outline-offset:2px}

/* shell */
.shell{max-width:1320px;margin:0 auto;padding:22px 24px 48px;display:grid;grid-template-columns:232px minmax(0,1fr);gap:24px;align-items:start}
.rail{position:sticky;top:76px;display:flex;flex-direction:column;gap:4px}
.rail h2{font-size:12px;font-weight:600;color:var(--faint);margin:0 0 6px 10px}
.rail .rail-h2{margin-top:22px}
.ri{display:grid;grid-template-columns:12px minmax(0,1fr);column-gap:10px;align-items:center;text-decoration:none;padding:9px 10px;border-radius:10px;border:1px solid transparent}
.ri:hover{background:var(--surface);border-color:var(--line)}
.ri-dot{grid-row:1/3;width:8px;height:8px;border-radius:50%;background:var(--faint);justify-self:center}
.ri-name{font-weight:700;font-size:14px}.ri-name em{font-style:normal;font-weight:500;color:var(--faint);font-size:11px;margin-left:6px}
.ri-sub{font-size:12px;color:var(--soft);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ri.on .ri-dot{background:var(--gold);box-shadow:0 0 0 3px var(--gold-2)}.ri.long .ri-dot{background:var(--up)}.ri.short .ri-dot{background:var(--down)}.ri.exit .ri-dot{background:var(--soft)}
.ri.on .ri-sub{color:var(--gold-ink)}
.rn{margin:0;padding:3px 10px;font-size:13px;color:var(--soft)}.rn b{color:var(--ink);font-weight:600}.rn-t{color:var(--gold-ink);font-weight:600}.rn.warn{color:var(--ink);background:color-mix(in srgb,var(--down) 12%,transparent);border-radius:8px;padding:8px 10px}
.nl{text-decoration:none;color:inherit;border-bottom:1px dotted var(--faint)}.nl:hover{color:var(--gold-ink);border-bottom-color:var(--gold)}
.nl::after{content:" ↗";font-size:10px;color:var(--faint)}
.rn.hot{color:var(--ink);background:color-mix(in srgb,var(--news) 16%,transparent);border-radius:8px;padding:8px 10px}

/* hero */
.main{min-width:0;display:flex;flex-direction:column;gap:14px}
.hero.calm{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 20px;padding:18px 22px;border-radius:16px;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow)}
.h-word{margin:0;font:800 28px/1 "Archivo",sans-serif;font-stretch:125%;color:var(--soft);letter-spacing:-.01em}
.h-sub{margin:0;color:var(--soft);font-size:14px;flex:1 1 360px}.h-sub b{color:var(--ink)}
.hero{display:grid;gap:12px}
.act{display:grid;grid-template-columns:minmax(150px,auto) minmax(0,1fr);gap:6px 24px;align-items:center;border-radius:16px;padding:18px 24px;border:1px solid var(--line);background:var(--surface);box-shadow:var(--shadow)}
.act p{margin:0}.act-word{font:800 46px/1 "Archivo",sans-serif;font-stretch:125%}
.act-head{font-weight:700;font-size:16px}.act-body p+p{margin-top:4px}.act-sm{opacity:.85;font-size:13px}
.act.flash{color:#fff;border:0}.act.flash .act-word{font-size:64px}
.act.flash.buy{background:var(--up);--fl:var(--up)}.act.flash.sell{background:var(--down);--fl:var(--down)}
.act.flash.exitf,.sigbtn.exitf{background:var(--exit);--fl:var(--exit)}.act.flash.exitf .act-word{font-size:56px}
.act.hold .act-word{color:var(--gold)}.act.hold.buy{box-shadow:inset 5px 0 0 var(--up),var(--shadow)}.act.hold.sell{box-shadow:inset 5px 0 0 var(--down),var(--shadow)}
.act.exit.win .act-word{color:var(--up)}.act.exit.loss .act-word{color:var(--down)}

/* strategy panels */
details.watch,section.watch{background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);overflow:hidden}
details.watch.on{border-color:color-mix(in srgb,var(--gold) 45%,var(--line))}
details.watch.long{border-color:color-mix(in srgb,var(--up) 55%,var(--line))}details.watch.short{border-color:color-mix(in srgb,var(--down) 55%,var(--line))}
.wh{list-style:none;display:grid;grid-template-columns:auto auto minmax(0,1fr) 18px;align-items:center;gap:16px;padding:14px 20px;cursor:pointer}
section.watch .wh{cursor:default;grid-template-columns:auto auto}
.wh::-webkit-details-marker{display:none}
details.watch>summary:hover{background:var(--hover)}
details.watch[open]>summary{border-bottom:1px solid var(--line)}
.w-id{display:flex;align-items:center;gap:8px}
.w-name{font:750 18px/1.1 "Archivo",sans-serif;font-stretch:118%;letter-spacing:-.01em;white-space:nowrap}
.paper,.live-tag{font-size:11px;font-weight:600;border-radius:6px;padding:2px 7px;letter-spacing:.01em}
.paper{color:var(--soft);border:1px solid var(--line-2)}.live-tag{color:var(--gold-ink);background:var(--gold-2)}
.w-state{justify-self:start;font-size:13px;font-weight:600;border-radius:999px;padding:4px 11px;white-space:nowrap;color:var(--soft);background:var(--hover);border:1px solid var(--line)}
.w-state.on{color:var(--gold-ink);background:var(--gold-2);border-color:transparent}
.w-state.long{color:var(--up);background:var(--up-bg);border-color:transparent}.w-state.short{color:var(--down);background:var(--down-bg);border-color:transparent}
.w-stats{justify-self:end;display:flex;gap:18px;color:var(--faint);font-size:12px;white-space:nowrap}
.w-stats b{color:var(--ink);font-weight:700;font-size:14px;margin-right:3px}.w-stats b.up{color:var(--up)}.w-stats b.down{color:var(--down)}
.chev{width:9px;height:9px;border-right:2px solid var(--soft);border-bottom:2px solid var(--soft);transform:rotate(45deg) translate(-2px,-2px);transition:transform .2s ease;justify-self:center}
details.watch[open]>summary .chev{transform:rotate(225deg) translate(-2px,-2px)}
.w-body{padding:18px 20px 16px}
.cols{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:28px}
.col-a,.col-b{min-width:0}
h3{font-size:12px;font-weight:600;color:var(--faint);margin:0 0 10px}
.w-meta{margin:14px 0 0;color:var(--faint);font-size:12px}

/* position */
.pos{border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--raise)}
.pos p{margin:0}
.pos.flat .pos-state{font:750 18px/1.2 "Archivo",sans-serif;font-stretch:115%;color:var(--soft)}
.pos-sub{color:var(--soft);font-size:13px;margin-top:2px!important}
.pos-prev{margin-top:10px!important;font-size:13px;color:var(--soft);padding-top:10px;border-top:1px solid var(--line)}.pos-prev b{font-weight:700}
.pos.long,.pos.short{display:grid;grid-template-columns:minmax(0,1fr) 170px;gap:14px}
.pos.long{background:linear-gradient(180deg,var(--up-bg),var(--raise) 70%)}.pos.short{background:linear-gradient(180deg,var(--down-bg),var(--raise) 70%)}
.pos-state{font:750 20px/1.2 "Archivo",sans-serif;font-stretch:115%}.pos.long .pos-state{color:var(--up)}.pos.short .pos-state{color:var(--down)}
.pnl{font:700 40px/1 "Archivo",sans-serif;font-stretch:120%;margin:12px 0 6px!important;letter-spacing:-.02em}
.pnl span{display:block;font:500 12px/1.4 "Archivo",sans-serif;font-stretch:100%;letter-spacing:0;color:var(--soft);margin-top:4px}
.money{list-style:none;padding:0;margin:0 0 8px;display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px}
.ladder{position:relative;min-height:180px;border-left:2px solid var(--line);margin-left:6px}
.zone{position:absolute;left:0;right:0}.zone.good{background:color-mix(in srgb,var(--up) 14%,transparent)}.zone.bad{background:color-mix(in srgb,var(--down) 14%,transparent)}
.lv{position:absolute;left:-2px;right:0;transform:translateY(-50%);display:flex;justify-content:space-between;align-items:center;font-size:12px;padding-left:10px;border-top:1px dashed var(--faint)}
.lv-k{color:var(--soft)}.lv-v{font-weight:700}
.lv-target{border-top-color:var(--up)}.lv-stop{border-top-color:var(--down)}.lv-target .lv-v{color:var(--up)}.lv-stop .lv-v{color:var(--down)}
.lv-now{border-top:2px solid var(--gold)}.lv-now .lv-k,.lv-now .lv-v{color:var(--gold)}
.lv-now::before{content:"";position:absolute;left:-6px;top:-6px;width:10px;height:10px;border-radius:50%;background:var(--gold)}

/* timeline */
.tl{margin-top:16px}
.tl-track{position:relative;height:10px;border-radius:999px;background:var(--hover);border:1px solid var(--line)}
.tl-win{position:absolute;top:-1px;bottom:-1px;border-radius:999px;background:color-mix(in srgb,var(--gold) 30%,transparent);border:1px solid var(--gold)}
.tl-news{position:absolute;top:-4px;bottom:-4px;border-radius:4px;background:repeating-linear-gradient(135deg,var(--news) 0 3px,transparent 3px 7px)}
.tl-now{position:absolute;top:-7px;bottom:-7px;width:2px;background:var(--ink);border-radius:2px}
.tl-now span{position:absolute;top:-17px;left:-12px;font-size:11px;color:var(--ink);font-weight:600}
.tl-trade{position:absolute;top:0;width:9px;height:9px;margin-left:-4px;border-radius:2px;transform:rotate(45deg)}
.tl-trade.long{background:var(--up)}.tl-trade.short{background:var(--down)}
.tl-ticks{position:relative;height:16px;margin-top:4px;font-size:11px;color:var(--faint)}.tl-ticks span{position:absolute;transform:translateX(-50%)}
.tl-ticks span:first-child{transform:none}.tl-ticks span:last-child{transform:translateX(-100%)}
.tl-cap{margin:4px 0 0;font-size:12px;color:var(--soft)}

/* next step */
.plain{margin-bottom:6px}
.kv{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:7px 0;margin:0;font-size:13px}
.plain .kv{display:block;padding:9px 0}.plain .kv:first-child{padding-top:0}
.plain .kv span{display:block;color:var(--faint);font-size:12px;margin-bottom:2px}
.plain .kv b{display:block;font-weight:600;line-height:1.45;font-size:14.5px}
.plain .kv.go b{color:var(--up)}
.sigbtn{display:flex;flex-direction:column;align-items:center;gap:4px;border-radius:14px;padding:16px 14px;margin:0 0 12px;color:#fff;text-align:center}
.sigbtn.buy{background:var(--up);--fl:var(--up)}.sigbtn.sell{background:var(--down);--fl:var(--down)}
.sigbtn .sb-word{font:800 32px/1 "Archivo",sans-serif;font-stretch:122%;letter-spacing:.02em}.sigbtn .sb-lv{font-weight:700;font-size:15px}.sigbtn .sb-sm{font-size:12px;opacity:.9}
.sigbtn.hold{background:var(--raise);color:var(--ink);border:2px solid var(--fl)}.sigbtn.hold .sb-word{color:var(--fl);font-size:24px}
.ready-badge{display:inline-block;margin:0 0 10px;padding:6px 14px;border-radius:999px;font-weight:800;font-size:13px;border:2px solid var(--up);color:var(--up);background:var(--up-bg)}
.ready-badge.sell{border-color:var(--down);color:var(--down);background:var(--down-bg)}
.live{border:1px dashed var(--gold);border-radius:10px;padding:4px 12px 10px;margin-bottom:6px;background:color-mix(in srgb,var(--gold-2) 40%,transparent)}
h4{margin:12px 0 8px;font-size:12px;font-weight:600;color:var(--soft)}h4 span{color:var(--faint);font-weight:400}
.meter{display:grid;grid-template-columns:44px minmax(0,1fr) 32px;align-items:center;gap:10px;margin-bottom:8px}
.m-l{font-weight:600;font-size:13px}.m-n{text-align:right;font-weight:700;color:var(--soft);font-size:13px}
.m-s{display:grid;grid-template-columns:repeat(8,1fr);gap:3px}
.m-s i{height:16px;border-radius:3px;background:var(--hover);border:1px solid var(--line)}
.meter.long .m-s i.on{background:var(--up);border-color:transparent}.meter.short .m-s i.on{background:var(--down);border-color:transparent}
.meter.ready .m-n{color:var(--gold)}
.miss{font-size:13px;margin:4px 0 2px}.hist{font-size:12px;color:var(--soft);margin:12px 0 0}
.cklist{list-style:none;margin:0;padding:0;font-size:13px}
.cklist li{display:grid;grid-template-columns:20px 20px minmax(0,1fr);align-items:center;padding:4px 0;border-bottom:1px solid var(--line)}
.cklist li.hd{color:var(--faint);font-size:11px}
.cklist .y::before{content:"✔";color:var(--up)}.cklist .n::before{content:"·";color:var(--faint);font-weight:700}

/* folding rows */
details.more,details.earlier{margin-top:12px}
details.more>summary,details.earlier>summary{list-style:none;cursor:pointer;color:var(--soft);font-size:13px;font-weight:600;padding:4px 0;display:inline-flex;align-items:center}
details.more>summary::-webkit-details-marker,details.earlier>summary::-webkit-details-marker,.al details>summary::-webkit-details-marker{display:none}
details.more>summary::before,details.earlier>summary::before,.al details>summary::before{content:"";display:inline-block;flex:none;width:6px;height:6px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);margin-right:10px;transition:transform .2s ease}
details.more[open]>summary::before,details.earlier[open]>summary::before,.al details[open]>summary::before{transform:rotate(45deg)}
details.more>summary:hover,details.earlier>summary:hover{color:var(--ink)}
details.trades{border-top:1px solid var(--line);margin-top:18px;padding-top:10px}

/* tables */
.tt{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed;margin-top:8px}
.tt th:nth-child(1){width:22%}.tt th:nth-child(2){width:13%}.tt th:nth-child(3),.tt th:nth-child(4){width:17%}.tt th:nth-child(5){width:15%}.tt th:nth-child(6){width:16%}
.tt th{font-weight:600;color:var(--faint);font-size:11.5px;text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}
.tt td{padding:8px 10px;border-bottom:1px solid var(--line)}.tt tr:last-child td{border-bottom:0}
.tt td:first-child{white-space:nowrap}.tt small{display:block;color:var(--faint);font-size:11.5px}
.tt tbody tr:hover{background:var(--hover)}
.tag{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11.5px;font-weight:700}
.tag.long{background:var(--up-bg);color:var(--up)}.tag.short{background:var(--down-bg);color:var(--down)}

/* activity */
.panel{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:18px 20px;box-shadow:var(--shadow)}
.panel h2{font:750 16px/1.2 "Archivo",sans-serif;font-stretch:115%;margin:0 0 10px}
.alerts{list-style:none;margin:0;padding:0}
.al{display:grid;grid-template-columns:84px minmax(0,1fr);gap:14px;padding:10px 0;border-bottom:1px solid var(--line)}
.al:last-child{border-bottom:0}
.al time{font-size:12px;color:var(--faint);line-height:1.35}
.al details>summary{list-style:none;cursor:pointer;color:var(--ink);font-weight:600;font-size:13.5px;display:flex;align-items:baseline}
.al p{margin:6px 0 0 16px;font-size:13px;color:var(--soft)}
.al.entry summary{color:var(--gold-ink)}.al.warning summary{color:var(--down)}.al.exit summary{color:var(--ink)}
.foot{color:var(--faint);font-size:12px;margin:6px 0 0;text-align:center}

/* mini chart */
.chart{margin:0 0 16px;border:1px solid var(--line);border-radius:12px;background:var(--raise);padding:10px 10px 6px}
.ch-plot{display:grid;grid-template-columns:minmax(0,1fr) 118px;height:190px}
.chart svg{display:block;width:100%;height:190px;overflow:visible}
.ch-lbl{position:relative;font-size:11.5px;font-weight:600;color:var(--soft)}
.ch-lbl .lb{position:absolute;left:8px;transform:translateY(-50%);white-space:nowrap;line-height:1}
.chart figcaption{display:flex;justify-content:space-between;gap:8px;font-size:11.5px;color:var(--faint);padding:6px 118px 0 0}
.chart .cu{stroke:var(--up);fill:var(--up)}.chart .cd{stroke:var(--down);fill:var(--down)}
.chart line.cu,.chart line.cd{vector-effect:non-scaling-stroke;stroke-width:1}.chart .cl{opacity:.55}
.chart .ln{fill:none;stroke-width:1.6;vector-effect:non-scaling-stroke}.chart .ln.series{stroke:var(--gold)}.chart .ln.series2{stroke:#6c8ff0}
.chart .lv2{stroke-width:1;stroke-dasharray:4 3;stroke:var(--faint);vector-effect:non-scaling-stroke}
.chart .lv2.trigger,.chart .lv2.entry{stroke:var(--gold)}.chart .lv2.level{stroke:var(--soft)}.chart .lv2.cancel,.chart .lv2.stop{stroke:var(--down)}
.chart .lv2.target{stroke:var(--up)}.chart .lv2.zone{stroke:#6c8ff0}.chart .lv2.now{stroke:var(--ink);stroke-dasharray:1 3;opacity:.6}
.chart .lv2.entry,.chart .lv2.stop,.chart .lv2.target{stroke-dasharray:none;stroke-width:1.4}
.lb.trigger,.lb.entry,.lb.series{color:var(--gold-ink)}.lb.cancel,.lb.stop{color:var(--down)}.lb.target{color:var(--up)}.lb.zone,.lb.series2{color:#6c8ff0}
.cd{font-weight:700;font-variant-numeric:tabular-nums}
/* today, journal, sound */
.today{margin:0;padding:10px 16px;border-radius:12px;background:var(--surface);border:1px solid var(--line);font-size:14px;color:var(--soft)}.today b{color:var(--ink)}.today b.up{color:var(--up)}.today b.down{color:var(--down)}
.jr{display:inline-flex;gap:4px;flex-wrap:wrap}
.jr button{font:inherit;font-size:11.5px;font-weight:600;padding:3px 8px;border-radius:7px;border:1px solid var(--line-2);background:transparent;color:var(--soft);cursor:pointer}
.jr button:hover{color:var(--ink);border-color:var(--soft)}
.jr button.on[data-c=took]{background:var(--up);border-color:var(--up);color:#fff}.jr button.on[data-c=skipped]{background:var(--soft);border-color:var(--soft);color:var(--surface)}
.sigbtn .jr{margin-top:8px}.sigbtn .jr button{color:inherit;border-color:currentColor;font-size:13px;padding:5px 12px}
.sigbtn.flash .jr button.on{background:#fff;color:#14181f;border-color:#fff}
.tt th:nth-child(7){width:150px}.jt th{width:auto!important}
.jr-sum{margin:0 0 6px;font-size:14px;color:var(--soft)}.jr-sum b{color:var(--ink)}
.tbtn{font:inherit;font-size:12px;color:var(--soft);background:transparent;border:1px solid var(--line-2);border-radius:8px;padding:5px 10px;cursor:pointer}
.tbtn:hover{color:var(--ink);border-color:var(--soft)}.tbtn:focus-visible,.jr button:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
#sound.on{color:var(--gold-ink);border-color:var(--gold);background:var(--gold-2)}
.foot .phone{display:block;margin-top:4px;word-break:break-all}
@media (max-width:620px){.ch-plot,.chart svg{height:150px}.ch-plot{grid-template-columns:minmax(0,1fr) 104px}.chart figcaption{padding-right:0}.chart figcaption span:first-child,.chart figcaption span:last-child{display:none}.tt th:nth-child(7){width:auto}.tt td:nth-child(7) .jr button{padding:3px 6px}}

/* motion */
@media (prefers-reduced-motion:no-preference){
.act.flash,.sigbtn.flash{animation:flash 1s steps(2,jump-none) infinite alternate}
@keyframes flash{from{background:var(--fl);box-shadow:0 0 0 0 transparent}to{background:color-mix(in srgb,var(--fl) 55%,var(--gold));box-shadow:0 0 0 6px color-mix(in srgb,var(--gold) 45%,transparent)}}
.ready-badge{animation:ready 1.4s ease-in-out infinite}@keyframes ready{0%,100%{opacity:1}50%{opacity:.4}}
.lv-now::before,.ri.on .ri-dot{animation:pulse 2.4s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--gold) 55%,transparent)}100%{box-shadow:0 0 0 8px transparent}}}
@media (prefers-reduced-motion:reduce){.chev,details.more>summary::before,details.earlier>summary::before,.al details>summary::before{transition:none}}

/* responsive */
@media (max-width:1100px){.w-stats span:nth-child(3){display:none}}
@media (max-width:980px){.shell{grid-template-columns:minmax(0,1fr);gap:14px}
  .rail{position:static;flex-direction:row;flex-wrap:nowrap;overflow-x:auto;gap:8px;padding-bottom:2px}
  .rail h2,.rail .rn{display:none}.ri{flex:0 0 auto;background:var(--surface);border-color:var(--line)}
  .tk-note{display:none}}
@media (max-width:820px){.cols{grid-template-columns:minmax(0,1fr);gap:18px}.w-stats{display:none}.wh{grid-template-columns:auto minmax(0,1fr) 18px}}
@media (max-width:620px){.top-in{flex-wrap:wrap;gap:8px 16px;padding:10px 16px}.tick{border-left:0;padding-left:0}.status{margin-left:0;width:100%;flex-wrap:wrap;gap:6px 10px;white-space:normal}.status #upd{flex:1 1 auto}.tbtn{padding:5px 8px}
  .shell{padding:14px 12px 40px}.wh{grid-template-columns:minmax(0,1fr) 18px;gap:8px 10px;padding:12px 14px}.w-state{grid-column:1;grid-row:2}
  .w-body{padding:14px}.act{grid-template-columns:minmax(0,1fr);padding:16px}.act.flash .act-word{font-size:46px}
  .tt th:nth-child(4),.tt td:nth-child(4){display:none}.pos.long,.pos.short{grid-template-columns:minmax(0,1fr)}.ladder{min-height:140px}
  .h-word{font-size:24px}}
"""


FRESH_MIN = 30  # a new entry signal flashes at the top of the page for this many minutes


def _exit_info(t) -> tuple[str, str, str]:
    """(big word, what happened, what you do) for a closed trade."""
    if t.exit_reason == "choch":
        return ("EXIT NOW", "trend broke (change of character)",
                "Close the remaining contracts <b>at market now</b> on Robinhood. No order is waiting for this exit.")
    if t.exit_reason == "time":
        return "EXIT NOW", "end-of-day exit", "Close the position <b>at market now</b> on Robinhood."
    if t.part_price is not None:
        return "CLOSED", "runner stopped at entry", "Your stop at entry should have closed the rest. Check Robinhood that you're flat."
    if t.exit_reason == "target":
        return "CLOSED", "target hit", "Your limit should have filled. Check Robinhood and <b>cancel the stop order</b>."
    return "CLOSED", "stop hit", "Your stop should have filled. Check Robinhood and <b>cancel the limit order</b>."


def _hold_text(t) -> str:
    if t.runner and t.part_price is not None:
        return (f"Target hit: {1 - t.runner:.0%} is closed. If you took this trade, keep the stop on the rest at your entry "
                f"({_px(t.entry_price or t.signal_close)}). Wait for the EXIT RUNNER alert. No new signals until it closes.")
    if t.runner:
        return (f"If you took this trade: stop {_px(t.stop)} for all contracts, limit {_px(t.target)} for {1 - t.runner:.0%} of them. "
                "If you didn't, do nothing: don't enter late.")
    return (f"If you took this trade: keep your stop at {_px(t.stop)} and limit at {_px(t.target)} (or the same distances from your fill). "
            "If you didn't, do nothing: don't enter late. No new signals until it closes.")


def _signal_button(lv) -> str:
    """Blinking BUY / SELL button in the watch panel while a new signal is fresh; HOLD while in a trade."""
    t = lv.res.open_trade if lv.res else None
    now_ms = time.time() * 1000
    if not t:
        last = lv.res.trades[-1] if lv.res and lv.res.trades else None
        if last and last.exit_time and now_ms - (last.exit_time + lv.book.bar_ms) <= FRESH_MIN * 60_000:
            word, why, todo = _exit_info(last)
            done = _clock(last.exit_time + lv.book.bar_ms)
            return (f'<div class="sigbtn flash exitf" role="alert" data-sig="exit|{E(lv.watch.name)}|{last.signal_time}">'
                    f'<span class="sb-word">{"PAPER " if lv.watch.paper else ""}{word}</span>'
                    f'<span class="sb-lv">{E(why.capitalize())} · {last.points:+.2f} pts at {_px(last.exit_price)}</span>'
                    f'<span class="sb-sm">{done} · {todo}</span></div>')
        return ""
    if t.part_time is not None and now_ms - (t.part_time + lv.book.bar_ms) <= FRESH_MIN * 60_000:
        return (f'<div class="sigbtn flash exitf" role="alert" data-sig="part|{E(lv.watch.name)}|{t.signal_time}">'
                f'<span class="sb-word">MOVE STOP TO {_px(t.entry_price or t.signal_close)}</span>'
                f'<span class="sb-lv">Target hit: {1 - t.runner:.0%} closed at {_px(t.part_price)}</span>'
                f'<span class="sb-sm">move the stop on the rest to your entry · then wait for the EXIT alert</span>{_jr(lv, t)}</div>')
    fired = t.signal_time + lv.book.bar_ms
    buy = t.direction > 0
    side = ("PAPER " if lv.watch.paper else "") + ("BUY" if buy else "SELL")
    mins = max(0, int((time.time() * 1000 - fired) // 60_000))
    levels = f"Entry {_px(t.signal_close)} · SL {_px(t.stop)} · TP {_px(t.target)}"
    if t.runner:
        levels += f" ({1 - t.runner:.0%} off, rest rides)"
    if t.part_price is not None:
        levels = f"{1 - t.runner:.0%} closed at {_px(t.part_price)} · runner stop at entry {_px(t.entry_price or t.signal_close)}"
    if mins <= FRESH_MIN and t.part_price is None:
        ago = "just now" if mins < 2 else f"{mins} min ago"
        return (f'<div class="sigbtn flash {"buy" if buy else "sell"}" role="alert" data-sig="{E(lv.watch.name)}|{t.signal_time}"><span class="sb-word">{side} NOW</span>'
                f'<span class="sb-lv">{levels}</span><span class="sb-sm">signal {_clock(fired)} ({ago}) · don\'t enter if price ran far past entry</span>{_jr(lv, t)}</div>')
    return (f'<div class="sigbtn hold {"buy" if buy else "sell"}"><span class="sb-word">HOLD {"LONG" if buy else "SHORT"}</span>'
            f'<span class="sb-lv">{levels}</span><span class="sb-sm">only if you took it · no new entry until it closes</span>{_jr(lv, t)}</div>')


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
        if t.part_time is not None and now_ms - (t.part_time + lv.book.bar_ms) <= FRESH_MIN * 60_000:
            ago = max(0, int((now_ms - (t.part_time + lv.book.bar_ms)) // 60_000))
            return f"""<div class="act flash exitf" role="alert">
  <p class="act-word">MOVE STOP</p>
  <div class="act-body"><p class="act-head">{name}: target hit {'just now' if ago < 2 else f'{ago} min ago'}, {1 - t.runner:.0%} closed at {_px(t.part_price)}</p>
  <p>Move the stop on the remaining contracts to your entry <b>{_px(t.entry_price or t.signal_close)}</b>. Then wait for the EXIT alert.</p></div></div>"""
        if mins <= FRESH_MIN:
            return f"""<div class="act flash {'buy' if buy else 'sell'}" role="alert">
  <p class="act-word">{'BUY' if buy else 'SELL'}</p>
  <div class="act-body"><p class="act-head">{name}, signal {_clock(fired)} ({'just now' if mins < 2 else f'{mins} min ago'})</p>
  <p>On Robinhood: {'buy' if buy else 'sell'} {E(names)}, then place a stop <b>{t.risk:.2f} pts {below}</b> and a limit <b>{t.reward:.2f} pts {above}</b> your fill.</p>
  <p class="act-sm">Signal close {_px(t.signal_close)}, stop {_px(t.stop)}, target {_px(t.target)}</p></div></div>"""
        return f"""<div class="act hold {'buy' if buy else 'sell'}">
  <p class="act-word">HOLD</p>
  <div class="act-body"><p class="act-head">{name}: {'long' if buy else 'short'} since {_clock(fired)}{'' if _day(fired) == 'Today' else ' ' + _day(fired)}</p>
  <p>{_hold_text(t)}</p></div></div>"""
    last = res.trades[-1] if res and res.trades else None
    if last and last.exit_time and now_ms - (last.exit_time + lv.book.bar_ms) <= FRESH_MIN * 60_000:
        word, why, todo = _exit_info(last)
        ago = max(0, int((now_ms - (last.exit_time + lv.book.bar_ms)) // 60_000))
        return f"""<div class="act flash exitf" role="alert">
  <p class="act-word">{word}</p>
  <div class="act-body"><p class="act-head">{name}: {why}, {last.points:+.2f} pts ({'just now' if ago < 2 else f'{ago} min ago'})</p>
  <p>{todo}</p></div></div>"""
    inwin = _in_window(lv, now)
    sub = "watching now" if inwin else _window(lv).replace(' and ', '–').replace(' CT', '')
    return f'<span class="chip{" on" if inwin else ""}"><b>{E(lv.watch.name.title())}</b> {sub}</span>'


def _chart(lv, bars, live_bar) -> str:
    """Inline SVG: recent candles plus the strategy's levels and the open trade's entry/stop/target."""
    n = 80 if lv.book.bar_ms <= 300_000 else 48
    allb = bars + ([live_bar] if live_bar is not None else [])
    view = allb[-n:]
    if len(view) < 5:
        return ""
    try:
        lv_lines = lv.strategy.levels(bars, Context(lv.symbol, {}))  # closed bars, same as "Next step"
    except Exception:
        lv_lines = []
    t = lv.res.open_trade if lv.res else None
    lines = []  # (label, price, kind)
    series = []  # (label, values aligned to view, kind)
    for label, val, kind in lv_lines:
        if isinstance(val, list):
            vals = val + [None] * (len(allb) - len(val))  # no value yet for the forming bar
            series.append((label, vals[-len(view):], kind))
        elif val is not None:
            lines.append((label, float(val), kind))
    if t:
        lines += [("Entry", t.entry_price or t.signal_close, "entry"), ("Stop", t.stop, "stop"), ("Target", t.target, "target")]
    lo = min(b.low for b in view)
    hi = max(b.high for b in view)
    rng = max(hi - lo, 1e-9)
    shown = [x for x in lines if lo - rng * 0.8 <= x[1] <= hi + rng * 0.8]
    for _, p, _ in shown:
        lo, hi = min(lo, p), max(hi, p)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    W, H, T, B = 600, 190, 8, 8  # plot area only; price labels sit in an HTML column on the right
    cw = W / len(view)

    def y(p: float) -> float:
        return T + (hi - p) / (hi - lo) * (H - T - B)

    out, labels = [], []  # labels: (y, text, kind)
    for i, b in enumerate(view):
        x = i * cw + cw / 2
        cls = "cu" if b.close >= b.open else "cd"
        if live_bar is not None and b is live_bar:
            cls += " cl"
        top, bot = y(max(b.open, b.close)), y(min(b.open, b.close))
        out.append(f'<line class="{cls}" x1="{x:.1f}" x2="{x:.1f}" y1="{y(b.high):.1f}" y2="{y(b.low):.1f}"/>'
                   f'<rect class="{cls}" x="{x - cw * 0.32:.1f}" y="{top:.1f}" width="{max(cw * 0.64, 1):.1f}" height="{max(bot - top, 0.8):.1f}"/>')
    for label, vals, kind in series:
        pts = " ".join(f"{i * cw + cw / 2:.1f},{y(v):.1f}" for i, v in enumerate(vals) if v is not None and lo <= v <= hi)
        if pts:
            out.append(f'<polyline class="ln {kind}" points="{pts}"/>')
            last = next((v for v in reversed(vals) if v is not None), None)
            if last is not None and lo <= last <= hi:
                labels.append((y(last), f"{label} {last:,.1f}", kind))
    for label, p, kind in shown:
        out.append(f'<line class="lv2 {kind}" x1="0" x2="{W}" y1="{y(p):.1f}" y2="{y(p):.1f}"/>')
        labels.append((y(p), f"{label} {p:,.1f}", kind))
    lastc = view[-1].close
    out.append(f'<line class="lv2 now" x1="0" x2="{W}" y1="{y(lastc):.1f}" y2="{y(lastc):.1f}"/>')
    tags, used = [], []
    for yy, text, kind in sorted(labels):  # top to bottom, pushed apart so they never overlap
        ty = max(yy, (used[-1] + 13) if used else 0)
        used.append(ty)
        tags.append(f'<span class="lb {kind}" style="top:{ty / H * 100:.1f}%">{E(text)}</span>')
    mins = lv.book.bar_ms // 60000
    cd = ""
    if live_bar is not None:
        cd = f' · bar closes in <span class="cd" data-t="{live_bar.time + lv.book.bar_ms}">–</span>'
    return (f'<figure class="chart"><div class="ch-plot"><svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" '
            f'aria-label="Last {len(view)} {mins}-minute candles with strategy levels">{"".join(out)}</svg>'
            f'<div class="ch-lbl">{"".join(tags)}</div></div>'
            f'<figcaption><span>{_clock(view[0].time)}</span><span>Last {len(view)} {mins}-min candles{cd}</span>'
            f'<span>{_clock(view[-1].time)}</span></figcaption></figure>')


def _next_open_ms(lv, now_ct) -> int | None:
    """Epoch ms of the next time this watch's entry window opens."""
    from datetime import timedelta

    p = lv.strategy.p
    a = getattr(p, "session_start", getattr(p, "from_hhmm", None))
    b = getattr(p, "session_end", getattr(p, "to_hhmm", None))
    if a is None or b is None:
        return None
    night = a > b
    for add in range(0, 8):
        d = (now_ct + timedelta(days=add)).replace(hour=a // 100, minute=a % 100, second=0, microsecond=0)
        if d <= now_ct:
            continue
        ok = d.weekday() in (6, 0, 1, 2, 3) if night else d.weekday() < 5
        if ok:
            return int(d.timestamp() * 1000)
    return None


def _today(lives, now_ct) -> str:
    """One line: today's signals across all strategies (CME day starts 17:00 CT the day before)."""
    from datetime import timedelta

    start = now_ct.replace(hour=17, minute=0, second=0, microsecond=0)
    if now_ct.hour < 17:
        start -= timedelta(days=1)
    s_ms = start.timestamp() * 1000
    n = won = lost = 0
    pts = usd = 0.0
    open_n = 0
    for lv in lives:
        if not lv.res:
            continue
        ppt = next(iter(contract_names(lv.watch, lv.symbol).values()), 0.0)
        for t in lv.res.trades:
            if t.signal_time < s_ms:
                continue
            n += 1
            if t.is_open:
                open_n += 1
            elif t.points is not None:
                won += t.points > 0
                lost += t.points <= 0
                pts += t.points
                usd += t.points * ppt
    if not n:
        return '<p class="today">Today: no signals yet.</p>'
    parts = [f"<b>{n}</b> signal{'s' if n != 1 else ''}", f"<b>{won}</b> won", f"<b>{lost}</b> lost"]
    if open_n:
        parts.append(f"<b>{open_n}</b> open")
    return (f'<p class="today">Today: {", ".join(parts)}. Paper result <b class="{"up" if pts >= 0 else "down"}">'
            f'{pts:+.1f} pts ({_usd(usd)} per micro)</b>.</p>')


def _trades_json(lives) -> str:
    rows = []
    for lv in lives:
        if not lv.res:
            continue
        ppt = next(iter(contract_names(lv.watch, lv.symbol).values()), 0.0)
        for t in lv.res.trades[-40:]:
            rows.append({"k": f"{lv.watch.name}|{t.signal_time}", "w": lv.watch.name.title(), "paper": lv.watch.paper,
                         "t": t.signal_time + lv.book.bar_ms, "side": "Long" if t.direction > 0 else "Short",
                         "entry": t.entry_price or t.signal_close, "pts": t.points, "usd": None if t.points is None else round(t.points * ppt, 2),
                         "res": t.exit_reason or "open"})
    return json.dumps(rows).replace("</", "<\\/")


def _ff(d) -> str:
    """ForexFactory calendar page for one day (times, forecasts and actual numbers)."""
    return f"https://www.forexfactory.com/calendar?day={d:%b}{d.day}.{d.year}".lower()


def _nl(d, text: str) -> str:
    return f'<a class="nl" href="{_ff(d)}" target="_blank" rel="noopener" title="Open the calendar for this day: forecast vs actual">{text}</a>'


def _rail(lives, now_ct, upcoming, news_today, info=None) -> str:
    items = []
    for lv in lives:
        slug = "w-" + "".join(c if c.isalnum() else "-" for c in lv.watch.name.lower())
        label, cls, _, _ = _state(lv, now_ct) if lv.res else ("Waiting for data", "off", False, False)
        items.append(f'<a class="ri {cls}" href="#{slug}"><span class="ri-dot"></span><span class="ri-name">{E(lv.watch.name.title())}'
                     f'{"" if not lv.watch.paper else "<em>paper</em>"}</span><span class="ri-sub">{E(label)}{_opens_in(lv, now_ct) if cls == "off" else ""}</span></a>')
    if news_today:
        news = f'<p class="rn hot"><b>{_nl(now_ct.date(), E(news_today) + " today, 7:30 CT")}.</b> Gold takes no new entries 7:00–8:00.</p>'
    elif upcoming:
        news = "".join(f'<p class="rn">{_nl(d, f"<b>{E(n)}</b> {d:%a %b %-d}")}</p>' for d, n in upcoming[:3])
    else:
        news = '<p class="rn muted">None scheduled</p>'
    other = ""
    for d in sorted(k for k in (info or {}) if k >= now_ct.date())[:3]:
        def upcoming_ev(t):
            if d != now_ct.date():
                return True
            try:
                tt = datetime.strptime(t, "%I:%M %p")
            except ValueError:
                return True
            return tt.hour * 60 + tt.minute > now_ct.hour * 60 + now_ct.minute
        # CPI / jobs reports already show under "News days"; past events today are dropped
        evs = [(t, n) for t, n in info[d] if upcoming_ev(t)
               and not any(k in n.lower() for k in ("cpi", "non-farm", "nonfarm", "nfp"))]
        if not evs:
            continue
        when = "Today" if d == now_ct.date() else ("Tomorrow" if (d - now_ct.date()).days == 1 else f"{d:%a %b %-d}")
        by_time: dict = {}
        for t, n in evs:
            by_time.setdefault(t, []).append(n)
        lines = "".join(_nl(d, f'<span class="rn-t">{E(t)}</span> {E(", ".join(ns))}') + "<br>" for t, ns in by_time.items())
        other += f'<p class="rn{" warn" if d == now_ct.date() else ""}"><b>{when}</b><br>{lines}</p>'
    other_html = (f'<h2 class="rail-h2">Other big news</h2>{other}<p class="rn muted small">Not blocked: signals still fire. Gold can spike at these times.</p>'
                  if other else "")
    return (f'<nav class="rail" aria-label="Strategies"><h2>Strategies</h2>{"".join(items)}'
            f'<h2 class="rail-h2">News days</h2>{news}<p class="rn muted small">Gold skips 7:00–8:00 CT on these mornings.</p>{other_html}</nav>')


def _ticker(lives) -> str:
    lv = next((x for x in lives if x.res and x.book.closed_bars()), None)
    if lv is None:
        return ""
    bars = lv.book.closed_bars()
    live_px, _ = _live(lv)
    px = live_px if live_px is not None else bars[-1].close
    now = datetime.now(CHICAGO)
    start = now.replace(hour=17, minute=0, second=0, microsecond=0)
    if now.hour < 17:
        from datetime import timedelta
        start -= timedelta(days=1)
    first = next((b for b in bars if b.time >= start.timestamp() * 1000), None)
    chg = (px - first.open) if first else 0.0
    return (f'<div class="tick"><span class="tk-sym">{E(lv.symbol)}</span><span class="tk-px">{_px(px)}</span>'
            f'<span class="tk-chg {"up" if chg >= 0 else "down"}">{"▲" if chg >= 0 else "▼"} {abs(chg):,.1f}</span>'
            f'<span class="tk-note">{"live" if live_px is not None else "last close"}, since 5 PM open</span></div>')


def _hero(lives, acts) -> str:
    big = [a for a in acts if not a.startswith('<span class="chip')]
    if big:
        return f'<section class="hero">{"".join(big)}</section>'
    now_ct = datetime.now(CHICAGO)
    watching = [lv for lv in lives if lv.res and _in_window(lv, now_ct)]
    later = [lv for lv in lives if lv.res and not _in_window(lv, now_ct)]
    parts = []
    if watching:
        parts.append(", ".join(f"<b>{E(lv.watch.name.title())}</b> is watching until {_window(lv).split(' and ')[1].replace(' CT', '')}" for lv in watching))
    if later:
        parts.append("next: " + ", ".join(f"{E(lv.watch.name.title())} at {_window(lv).split(' and ')[0]}{_opens_in(lv, now_ct)}" for lv in later))
    return (f'<section class="hero calm"><p class="h-word">No action</p>'
            f'<p class="h-sub">{"; ".join(parts) or "No strategies running."}. You will get a phone alert the moment a signal fires.</p></section>')


def render(lives, nd: dict[date, str], alerts: list[dict], started: float, mkt_open: bool, info=None,
           port: int = 0, links: list[str] | None = None) -> str:
    now_ct = datetime.now(CHICAGO)
    today = now_ct.date()
    past_news = now_ct.hour >= 8  # today's 7:30 release and the 7:00-8:00 block are over
    upcoming = sorted((d, n) for d, n in nd.items() if d > today or (d == today and not past_news))[:5]
    news_today = None if past_news else nd.get(today)
    watches = "".join(_watch(lv, nd) for lv in lives)
    acts = [_action(lv) for lv in lives if lv.res]
    title = "Signal Desk"
    for lv, a in zip([lv for lv in lives if lv.res], acts):
        if 'class="act flash exitf' in a:
            title = f"{'MOVE STOP' if lv.res.open_trade else 'EXIT'} {lv.symbol} · Signal Desk"
            break
        if 'class="act flash' in a:
            title = f"{'BUY' if lv.res.open_trade.direction > 0 else 'SELL'} {lv.symbol} · Signal Desk"
            break
    now = time.time()
    phone = ""
    if links and len(links) > 1:
        phone = f' <span class="phone">On your phone (same Wi-Fi): <b>{E(links[1])}</b></span>'
    return f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<noscript><meta http-equiv="refresh" content="15"></noscript><title>{E(title)}</title>
<script>try{{var t=localStorage.getItem('sd:theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..800&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header class="top"><div class="top-in">
<p class="brand"><span class="ingot" aria-hidden="true"></span>Signal Desk</p>
{_ticker(lives)}
<p class="status"><span class="mk{' on' if mkt_open else ''}"><span class="dot"></span>Market {'open' if mkt_open else 'closed'}</span>
<span id="upd" data-t="{int(now)}">Updated {_clock(now)} CT</span>
<button id="sound" class="tbtn" type="button" aria-label="Play a chime when a new signal fires">Enable sound</button>
<button id="theme" class="tbtn" type="button" aria-label="Switch light or dark theme">Theme</button></p>
</div></header>
<div class="shell">
{_rail(lives, now_ct, upcoming, news_today, info or {})}
<main class="main">
{_hero(lives, acts)}
{_today(lives, now_ct)}
{watches}
<section class="panel journal"><h2>My journal</h2><div id="jr-body"></div></section>
<section class="panel activity"><h2>Activity</h2>{_alerts(alerts)}</section>
<p class="foot">Signals only, not trading advice. You place trades yourself on Robinhood. Running since {_clock(started)}{'' if _day(started) == 'Today' else ', ' + _day(started)}. Updates every 10 seconds.{phone}</p>
</main></div>
<script id="trades-data" type="application/json">{_trades_json(lives)}</script>
<script>__JS__</script></body></html>""".replace("__JS__", JS.replace("__PORT__", str(port or 8765)))


def write(path: Path, lives, nd, alerts, started, mkt_open, info=None, port: int = 0, links: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render(lives, nd, alerts, started, mkt_open, info, port, links), encoding="utf-8")
    os.replace(tmp, path)


JS = r"""(function(){
var D=document;
function g(k){try{return localStorage.getItem(k)}catch(x){return null}}
function st(k,v){try{localStorage.setItem(k,v)}catch(x){}}
var HTTP=location.protocol.indexOf('http')===0, Q=location.search||'', journal={}, ctx=null;
function fmt(ms){if(ms<=0)return 'now';var s=Math.floor(ms/1000),h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?h+'h '+(m<10?'0':'')+m+'m':m+':'+(x<10?'0':'')+x}
function tick(){var n=Date.now();D.querySelectorAll('.cd[data-t]').forEach(function(e){e.textContent=fmt(+e.dataset.t-n)})}
function stale(){var e=D.getElementById('upd');if(!e)return;var m=Math.floor((Date.now()/1000-(+e.dataset.t))/60);if(m>=3){e.textContent='Not updated for '+m+' min: the bot may have stopped';e.className='stale'}}
function restore(){D.querySelectorAll('details[data-key]').forEach(function(d){var k='sd:'+d.dataset.key,v=g(k);if(d.dataset.force==='1'){d.open=true}else if(v!==null){d.open=v==='1'}d.addEventListener('toggle',function(){st(k,d.open?'1':'0')})});
 D.querySelectorAll('.ri').forEach(function(a){a.addEventListener('click',function(){var d=D.querySelector(a.getAttribute('href'));if(d&&d.tagName==='DETAILS')d.open=true})})}
function theme(){var tb=D.getElementById('theme');if(!tb)return;tb.textContent=D.documentElement.dataset.theme==='light'?'Dark mode':'Light mode';tb.onclick=function(){var n=D.documentElement.dataset.theme==='light'?'dark':'light';D.documentElement.dataset.theme=n;st('sd:theme',n);theme()}}
function chime(){if(!ctx)return;var t=ctx.currentTime;[880,1320].forEach(function(f,i){var o=ctx.createOscillator(),gn=ctx.createGain();o.type='sine';o.frequency.value=f;var s=t+i*0.18;gn.gain.setValueAtTime(0.0001,s);gn.gain.exponentialRampToValueAtTime(0.3,s+0.02);gn.gain.exponentialRampToValueAtTime(0.0001,s+0.5);o.connect(gn);gn.connect(ctx.destination);o.start(s);o.stop(s+0.55)})}
function soundBtn(){var b=D.getElementById('sound');if(!b)return;if(!HTTP){b.hidden=true;return}var on=!!ctx&&g('sd:sound')==='1';b.textContent=on?'Sound on':'Enable sound';b.classList.toggle('on',on);
 b.onclick=function(){if(!ctx){try{ctx=new (window.AudioContext||window.webkitAudioContext)()}catch(x){}}if(b.classList.contains('on')){st('sd:sound','0')}else{st('sd:sound','1');if(ctx){ctx.resume();chime()}}soundBtn()}}
function checkSignal(){D.querySelectorAll('[data-sig]').forEach(function(e){var k='sd:chimed:'+e.dataset.sig;if(g(k))return;st(k,'1');if(ctx&&g('sd:sound')==='1')chime()})}
function money(v){return (v<0?'-$':'+$')+Math.abs(v).toFixed(0)}
function jrPaint(){D.querySelectorAll('.jr[data-k]').forEach(function(el){var c=(journal[el.dataset.k]||{}).choice||'';el.querySelectorAll('button').forEach(function(b){b.classList.toggle('on',b.dataset.c===c)})});
 var body=D.getElementById('jr-body');if(!body||!HTTP)return;var rows=[];try{rows=JSON.parse(D.getElementById('trades-data').textContent)}catch(x){}
 var mine=rows.filter(function(r){return journal[r.k]}).sort(function(a,b){return b.t-a.t});
 if(!mine.length){body.innerHTML='<p class="muted">Mark a signal with <b>Took it</b> or <b>Skipped</b> (on the signal or in Recent trades) to start your journal.</p>';return}
 var took=mine.filter(function(r){return journal[r.k].choice==='took'}),sk=mine.filter(function(r){return journal[r.k].choice==='skipped'});
 function sum(a){return a.reduce(function(s,r){return s+(r.usd||0)},0)}
 var h='<p class="jr-sum">Took <b>'+took.length+'</b>: '+money(sum(took))+' per micro if filled like the bot. Skipped <b>'+sk.length+'</b>: those would have made '+money(sum(sk))+'.</p><table class="tt jt"><thead><tr><th>Signal</th><th>Strategy</th><th>Side</th><th>Result</th><th class="num">$ per micro</th><th>You</th></tr></thead><tbody>';
 mine.slice(0,15).forEach(function(r){var d=new Date(r.t);h+='<tr><td>'+d.toLocaleDateString([],{month:'short',day:'numeric'})+'<small>'+d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})+'</small></td><td>'+r.w+(r.paper?' <small>paper</small>':'')+'</td><td><span class="tag '+r.side.toLowerCase()+'">'+r.side+'</span></td><td>'+r.res+'</td><td class="num '+((r.usd||0)>=0?'up':'down')+'">'+(r.usd==null?'open':money(r.usd))+'</td><td>'+(journal[r.k].choice==='took'?'Took it':'Skipped')+'</td></tr>'});
 body.innerHTML=h+'</tbody></table>'}
function jrLoad(){if(!HTTP){D.querySelectorAll('.jr').forEach(function(e){e.hidden=true});var b=D.getElementById('jr-body');if(b)b.innerHTML='<p class="muted">Open the dashboard at <b>http://localhost:__PORT__/</b> to use the journal and sound.</p>';return}
 fetch('/api/journal'+Q,{cache:'no-store'}).then(function(r){return r.json()}).then(function(j){journal=j;jrPaint()}).catch(function(){})}
function jrBind(){D.querySelectorAll('.jr[data-k] button').forEach(function(b){b.onclick=function(ev){ev.preventDefault();ev.stopPropagation();var k=b.closest('.jr').dataset.k,c=b.classList.contains('on')?'':b.dataset.c;
 fetch('/api/journal'+Q,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:k,choice:c})}).then(function(r){return r.json()}).then(function(j){journal=j;jrPaint()}).catch(function(){})}})}
function init(){restore();theme();soundBtn();jrBind();jrPaint();tick();stale();checkSignal()}
window._bt=D.title;init();jrLoad();setInterval(tick,1000);setInterval(stale,20000);
setInterval(function(){var f=D.querySelector('.act.flash');if(!f){D.title=window._bt;return}D.title=D.title.indexOf('🔔')===0?window._bt:'🔔 '+window._bt},900);
if(HTTP){setInterval(function(){fetch(location.pathname+Q,{cache:'no-store'}).then(function(r){return r.text()}).then(function(html){var doc=new DOMParser().parseFromString(html,'text/html');
 ['.top-in','.shell'].forEach(function(sel){var a=D.querySelector(sel),b=doc.querySelector(sel);if(a&&b)a.innerHTML=b.innerHTML});
 var td=doc.getElementById('trades-data');if(td)D.getElementById('trades-data').textContent=td.textContent;window._bt=doc.title;init()}).catch(function(){})},10000)}
else{try{var y=sessionStorage.getItem('sd:y');if(y!==null)window.scrollTo(0,+y)}catch(x){}setTimeout(function(){try{sessionStorage.setItem('sd:y',String(window.scrollY))}catch(x){}location.reload()},15000)}
})();
"""
