"""HTTPS helper: verify certificates with certifi's CA bundle.

Python from python.org on macOS has no CA certificates until "Install Certificates.command"
is run, so plain urllib fails with CERTIFICATE_VERIFY_FAILED. certifi (already installed
with tastytrade/httpx) fixes that everywhere.
"""

from __future__ import annotations

import ssl
import urllib.request

try:
    import certifi

    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # certifi missing: fall back to the system store
    _CTX = ssl.create_default_context()


def urlopen(req: urllib.request.Request | str, timeout: float = 15.0):
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX)
