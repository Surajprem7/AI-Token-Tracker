"""Exchange rates for showing costs in your own currency.

Downloaded only after you pick a currency other than US dollars, from the free
open.er-api.com service (no account, nothing about your usage is sent), and kept
for 12 hours in ~/.ai-token-tracker/rates.json so the app works offline.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from . import __version__

URL = "https://open.er-api.com/v6/latest/USD"
MAX_AGE = 12 * 3600


def cache_file() -> Path:
    return Path(os.environ.get("AI_TOKEN_TRACKER_DIR") or Path.home() / ".ai-token-tracker").expanduser() / "rates.json"


def _valid(doc) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("rates"), dict) and doc["rates"].get("USD") == 1
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in doc["rates"].values()))


def get(force: bool = False) -> dict:
    """{rates: {CODE: units per USD}, updated: epoch seconds} or {error}."""
    path = cache_file()
    cached = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if _valid(cached) and not force and time.time() - cached.get("fetched", 0) < MAX_AGE:
        return {"rates": cached["rates"], "updated": cached.get("updated")}
    try:
        from .pricing import _ssl_context

        req = urllib.request.Request(URL, headers={"User-Agent": f"ai-token-tracker/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:  # noqa: S310 - fixed https URL
            body = json.loads(resp.read(1024 * 1024).decode("utf-8"))
        if body.get("result") != "success" or not _valid(body):
            raise ValueError("unexpected reply")
        doc = {"rates": body["rates"], "updated": body.get("time_last_update_unix"), "fetched": time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        return {"rates": doc["rates"], "updated": doc["updated"]}
    except Exception as exc:
        if _valid(cached):  # offline: an older rate beats none
            return {"rates": cached["rates"], "updated": cached.get("updated"), "stale": True}
        return {"error": f"Couldn't download exchange rates: {exc}"}
