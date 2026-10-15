"""Tiny local web server for the dashboard (standard library only).

* http://localhost:<port>/            the dashboard (updates in place, so sound + scroll work)
* GET/POST /api/journal               your "took it / skipped" marks, saved in the state dir
* Phone access (optional, ``dashboard_lan = true``): the server also listens on your Wi-Fi
  address; requests from other devices must carry ``?k=<token>`` (shown in the log and on the
  dashboard footer when you open it on the Mac).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)
CHOICES = ("took", "skipped", "")


def lan_ip() -> str:
    """This machine's address on the local network (no traffic is sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_token(state_dir: Path, configured: str = "") -> str:
    if configured:
        return configured
    p = state_dir / "dashboard_token"
    try:
        return p.read_text().strip()
    except Exception:
        tok = secrets.token_urlsafe(9)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tok)
        os.chmod(p, 0o600)
        return tok


class Journal:
    """{"<watch>|<signal time ms>": {"choice": "took" | "skipped", "t": unix}} in a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def set(self, key: str, choice: str) -> dict:
        import time

        with self.lock:
            data = self.read()
            if choice:
                data[key] = {"choice": choice, "t": time.time()}
            else:
                data.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.path)
            return data


def make_handler(html_path: Path, journal: Journal, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SignalDesk"

        def log_message(self, fmt, *args):  # keep the bot log clean
            log.debug("dashboard http: " + fmt, *args)

        def _local(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1")

        def _authorized(self) -> bool:
            if self._local():
                return True
            q = parse_qs(urlparse(self.path).query)
            return bool(token) and secrets.compare_digest(q.get("k", [""])[0], token)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if not self._authorized():
                return self._send(403, b"Forbidden: add ?k=<token> from the dashboard footer", "text/plain")
            path = urlparse(self.path).path
            if path in ("/", "/dashboard.html"):
                try:
                    return self._send(200, html_path.read_bytes(), "text/html; charset=utf-8")
                except FileNotFoundError:
                    return self._send(503, b"Dashboard not written yet, try again in a few seconds", "text/plain")
            if path == "/api/journal":
                return self._send(200, json.dumps(journal.read()).encode(), "application/json")
            self._send(404, b"Not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if not self._authorized():
                return self._send(403, b"Forbidden", "text/plain")
            if urlparse(self.path).path != "/api/journal":
                return self._send(404, b"Not found", "text/plain")
            try:
                n = min(int(self.headers.get("Content-Length", "0")), 4096)
                req = json.loads(self.rfile.read(n) or b"{}")
                key, choice = str(req.get("key", ""))[:120], str(req.get("choice", ""))
                if not key or choice not in CHOICES:
                    raise ValueError("bad request")
            except Exception:
                return self._send(400, b"Bad request", "text/plain")
            self._send(200, json.dumps(journal.set(key, choice)).encode(), "application/json")

    return Handler


def start(html_path: Path, state_dir: Path, port: int, lan: bool, token: str) -> list[str]:
    """Start the server in a background thread. Returns the URLs it answers on ([] on failure)."""
    journal = Journal(state_dir / "journal.json")
    handler = make_handler(html_path, journal, token if lan else "")
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), handler)
    except OSError as exc:
        log.warning("dashboard server not started on port %s: %s", port, exc)
        return []
    threading.Thread(target=httpd.serve_forever, name="dashboard-http", daemon=True).start()
    urls = [f"http://localhost:{port}/"]
    if lan:
        urls.append(f"http://{lan_ip()}:{port}/?k={token}")
    return urls
