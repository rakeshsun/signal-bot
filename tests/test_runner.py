import asyncio
import json
import time

from signalbot import dashboard, strategies
from signalbot.config import Watch
from signalbot.notify import NotifyConfig, Notifier
from signalbot.runner import Alerts, Live, SentLog, _baseline

from helpers import make_bars, random_walk


class FakeBook:
    def __init__(self, bars):
        self.bars, self.bar_ms, self.restarts = bars, 900_000, 0
        self.ready = asyncio.Event()
        self.ready.set()

    def closed_bars(self):
        return list(self.bars)

    def __len__(self):
        return len(self.bars)


def _live(bars):
    w = Watch(name="gold", strategy="metals_confluence", contracts={"/MGC": 10, "/GC": 100})
    cls = strategies.get(w.strategy)
    return Live(w, cls(), "/GCZ6", None, FakeBook(bars), task=None)


def test_sentlog_migrates_old_list_and_writes_atomically(tmp_path):
    p = tmp_path / "sent.json"
    p.write_text(json.dumps(["a", "b"]))
    s = SentLog(p)
    assert "a" in s and s.add("c") and not s.add("c")
    assert set(json.loads(p.read_text())) == {"a", "b", "c"}
    assert not (tmp_path / "sent.tmp").exists()


def test_alert_not_marked_sent_when_delivery_fails(tmp_path, monkeypatch):
    n = Notifier(NotifyConfig(ntfy_topic="x", macos_banner=False))
    monkeypatch.setattr(Notifier, "_post", staticmethod(lambda req, name, tries=3: False))
    sent = SentLog(tmp_path / "sent.json")
    a = Alerts(n, sent, tmp_path / "alerts.jsonl")
    assert asyncio.run(a.send("k1", "t", "b")) is False and "k1" not in sent
    monkeypatch.setattr(Notifier, "_post", staticmethod(lambda req, name, tries=3: True))
    assert asyncio.run(a.send("k1", "t", "b")) is True and "k1" in sent
    assert asyncio.run(a.send("k1", "t", "b")) is False  # never twice
    assert len(a.recent) == 1


def test_baseline_leaves_last_bar_for_the_loop(tmp_path):
    c, v = random_walk()
    bars = make_bars(c, vol=v)
    # cut the series right after an entry signal bar so the last closed bar carries the entry
    lv = _live(bars)
    full = asyncio.run(_run_baseline(lv, tmp_path))
    entry_bar = full.trades[2].signal_index
    lv2 = _live(bars[: entry_bar + 1])
    sent = SentLog(tmp_path / "s2.json")
    asyncio.run(_baseline(lv2, {}, sent))
    key = f"gold|entry|{bars[entry_bar].time}"
    assert key not in sent  # still to be delivered after a restart
    assert f"gold|entry|{full.trades[0].signal_time}" in sent  # older ones are baseline


async def _run_baseline(lv, tmp_path):
    await _baseline(lv, {}, SentLog(tmp_path / "s1.json"))
    return lv.res


def test_dashboard_renders(tmp_path):
    c, v = random_walk()
    lv = _live(make_bars(c, vol=v))
    asyncio.run(_baseline(lv, {}, SentLog(tmp_path / "s.json")))
    out = tmp_path / "d.html"
    alerts = [{"t": time.time(), "kind": "entry", "title": "BUY /GCZ6 — gold 8/8", "body": "stop 14 pts"}]
    dashboard.write(out, [lv], {}, alerts, time.time() - 3600, True)
    html = out.read_text()
    assert "Signal" in html and "BUY /GCZ6" in html and "Next step" in html and "Recent trades" in html


def test_high_impact_news_info_is_separate_from_blocking_days():
    from datetime import date as _d

    from signalbot import messages, news

    events = [
        {"country": "USD", "impact": "High", "title": "PPI m/m", "date": "2026-10-15T08:30:00-04:00"},
        {"country": "USD", "impact": "High", "title": "Retail Sales m/m", "date": "2026-10-15T08:30:00-04:00"},
        {"country": "USD", "impact": "High", "title": "Fed Chairman Speaks", "date": "2026-10-15T23:30:00-04:00"},
        {"country": "USD", "impact": "Medium", "title": "Empire State", "date": "2026-10-15T08:30:00-04:00"},
        {"country": "EUR", "impact": "High", "title": "ECB", "date": "2026-10-15T08:30:00-04:00"},
    ]
    assert news.parse_ff(events) == {}  # PPI / retail sales don't block entries
    info = news.parse_ff_info(events)
    assert info == {_d(2026, 10, 15): [("7:30 AM", "PPI m/m"), ("7:30 AM", "Retail Sales m/m"), ("10:30 PM", "Fed Chairman Speaks")]}
    title, body = messages.news_info(info[_d(2026, 10, 15)])
    assert "not blocked" in title and "7:30 AM CT  PPI m/m" in body


def test_dashboard_has_chart_journal_and_today(tmp_path):
    c, v = random_walk()
    lv = _live(make_bars(c, vol=v))
    asyncio.run(_baseline(lv, {}, SentLog(tmp_path / "s.json")))
    html = dashboard.render([lv], {}, [], time.time(), True, None, 8765, ["http://localhost:8765/", "http://10.0.0.5:8765/?k=abc"])
    assert '<figure class="chart">' in html and "<svg" in html
    assert 'id="jr-body"' in html and 'id="trades-data"' in html and 'class="jr"' in html
    assert 'class="today"' in html and 'id="sound"' in html
    assert "10.0.0.5:8765/?k=abc" in html and "__PORT__" not in html and "__JS__" not in html
    data = html.split('id="trades-data" type="application/json">')[1].split("</script>")[0]
    rows = json.loads(data)
    assert rows and {"k", "w", "t", "side", "usd"} <= set(rows[0])


def test_dashboard_server_serves_page_and_journal(tmp_path):
    import socket
    import urllib.error
    import urllib.request

    from signalbot import server

    page = tmp_path / "d.html"
    page.write_text("<html>hello</html>")
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    urls = server.start(page, tmp_path, port, False, "")
    assert urls == [f"http://localhost:{port}/"]
    base = f"http://127.0.0.1:{port}"
    assert b"hello" in urllib.request.urlopen(base + "/").read()
    assert json.loads(urllib.request.urlopen(base + "/api/journal").read()) == {}

    def post(body):
        req = urllib.request.Request(base + "/api/journal", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return json.loads(urllib.request.urlopen(req).read())

    assert post({"key": "gold|1", "choice": "took"})["gold|1"]["choice"] == "took"
    assert json.loads((tmp_path / "journal.json").read_text())["gold|1"]["choice"] == "took"
    assert post({"key": "gold|1", "choice": ""}) == {}
    try:
        post({"key": "gold|1", "choice": "maybe"})
        raise AssertionError("bad choice accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_dashboard_server_lan_needs_token_and_known_host(tmp_path):
    from signalbot import server

    tok = server.load_token(tmp_path)
    assert tok and server.load_token(tmp_path) == tok  # saved once, reused
    h = server.make_handler(tmp_path / "d.html", server.Journal(tmp_path / "j.json"), tok, {"192.168.1.5"})

    def req(client, path, host, origin=None):
        fake = h.__new__(h)
        fake.client_address, fake.path = (client, 5000), path
        fake.headers = {"Host": host, **({"Origin": origin} if origin else {})}
        return fake._authorized()

    assert not req("192.168.1.20", "/", "192.168.1.5:8765")
    assert req("192.168.1.20", f"/?k={tok}", "192.168.1.5:8765")
    assert not req("192.168.1.20", "/?k=caf\u00e9", "192.168.1.5:8765")  # non-ASCII key: refused, no crash
    assert req("127.0.0.1", "/", "localhost:8765")
    assert not req("127.0.0.1", "/", "evil.example:8765")  # DNS rebinding
    assert not req("127.0.0.1", "/api/journal", "localhost:8765", origin="https://evil.example")  # cross-site POST
    assert req("127.0.0.1", "/api/journal", "localhost:8765", origin="http://localhost:8765")


def test_bos_run_cache_keeps_closed_and_live_answers():
    c, v = random_walk()
    bars = make_bars(c, vol=v)
    s = strategies.get("bos_pullback")()
    a = s._run(bars[:-1])
    b = s._run(bars)
    assert s._run(bars[:-1]) is a and s._run(bars) is b and len(s._run_cache) == 2


def test_exit_alert_only_for_announced_trades(tmp_path):
    from types import SimpleNamespace

    from signalbot.runner import entry_announced

    sent = SentLog(tmp_path / "s.json")
    t = SimpleNamespace(signal_time=123)
    assert not entry_announced(sent, "gold", t)  # began and ended inside a data gap: stay quiet
    sent.add("gold|entry|123")
    assert entry_announced(sent, "gold", t) and not entry_announced(sent, "gold-bos", t)


def test_size_sets_runner_contracts_and_alert_totals():
    from signalbot import messages
    from signalbot.engine import Result, Trade
    from signalbot.runner import apply_size

    B = strategies.get("bos_pullback")
    for size, expect in ((1, 0.0), (2, 0.5), (3, 1 / 3), (4, 0.5)):
        s = B(B.make_params({"runner": 0.5}))
        apply_size(s, Watch(name="g", strategy="bos_pullback", size=size))
        assert s.p.runner == expect
    w = Watch(name="gold-bos", strategy="bos_pullback", contracts={"/MGC": 10, "/GC": 100}, size=4)
    t = Trade(1, 0, 0, 4190.0, 4174.0, 4210.0, {}, 1, 4190.0, runner=0.5)
    title, body = messages.entry(t, w, "/GCZ6", Result([]), 180_000)
    assert title.startswith("BUY 4x 4,190.0") and "risk $640 total" in body and "2 exit at TP, 2 ride" in body
    t.part_price = 4210.0
    assert "2 /MGCZ26 closed +20.00 pts = +$400" in messages.partial(t, w, "/GCZ6")[1]
    t.exit_price, t.exit_reason, t.exit_index = 4230.0, "choch", 9
    assert "4 /MGCZ26: +$1,200 total" in messages.exit_(t, w, "/GCZ6")[1]
