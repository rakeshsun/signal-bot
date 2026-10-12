"""Where alerts go: the console always, plus ntfy (phone push), Telegram, and/or a macOS banner."""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import sys
import urllib.parse
import urllib.request

from .net import urlopen
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class NotifyConfig:
    ntfy_topic: str = ""  # e.g. "rakesh-gold-7f3k2"; subscribe to it in the ntfy phone app
    ntfy_server: str = "https://ntfy.sh"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    macos_banner: bool = True


class Notifier:
    def __init__(self, cfg: NotifyConfig, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run

    def send(self, title: str, body: str, urgent: bool = False) -> None:
        if sys.stdout.isatty():
            print(f"\n=== {title} ===\n{body}\n", flush=True)
        log.info("ALERT %s | %s", title, body.replace("\n", " / "))
        if self.dry_run:
            return
        if self.cfg.ntfy_topic:
            self._ntfy(title, body, urgent)
        if self.cfg.telegram_token and self.cfg.telegram_chat_id:
            self._telegram(f"{title}\n{body}")
        if self.cfg.macos_banner and platform.system() == "Darwin":
            self._macos(title, body)

    def _ntfy(self, title: str, body: str, urgent: bool) -> None:
        url = f"{self.cfg.ntfy_server.rstrip('/')}/{self.cfg.ntfy_topic}"
        req = urllib.request.Request(
            url,
            data=body.encode(),
            headers={
                "Title": title.encode("ascii", "ignore").decode(),
                "Priority": "urgent" if urgent else "default",
                "Tags": "moneybag" if urgent else "bell",
            },
            method="POST",
        )
        self._post(req, "ntfy")

    def _telegram(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage"
        data = json.dumps({"chat_id": self.cfg.telegram_chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        self._post(req, "telegram")

    @staticmethod
    def _post(req: urllib.request.Request, name: str) -> None:
        try:
            with urlopen(req, timeout=15):
                pass
        except Exception as exc:
            log.warning("%s notification failed: %s", name, exc)

    @staticmethod
    def _macos(title: str, body: str) -> None:
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        script = f'display notification "{esc(body[:200])}" with title "{esc(title)}" sound name "Glass"'
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=10)
        except Exception as exc:
            log.warning("macOS notification failed: %s", exc)
