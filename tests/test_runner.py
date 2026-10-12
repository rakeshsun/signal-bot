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
    assert "Signal" in html and "BUY /GCZ6" in html and "Ready to enter" in html and "Last " in html
