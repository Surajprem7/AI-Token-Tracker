"""Estimated cost per API call.

Prices come from three layers, later ones winning:

1. data/prices.json - a snapshot bundled with the app, so costs work offline.
2. ~/.ai-token-tracker/prices-cache.json - written only when you ask for an
   update (`ai-tokens --update-prices` or the button in the app). It is a
   trimmed copy of LiteLLM's public model price list.
3. ~/.ai-token-tracker/prices.json - your own overrides, in USD per 1M tokens:
   {"my-model": {"input": 1.0, "output": 4.0, "cache_read": 0.1, "cache_write": 1.25}}

All costs are estimates: list prices, no discounts, plans or batch rates.
Subscription plans (Claude Max, ChatGPT Pro, ...) are billed differently; the
figure then shows what the same usage would cost on the pay-as-you-go API.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .core import Usage

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
PROVIDERS = {"anthropic", "openai", "gemini", "xai", "deepseek", "mistral", "moonshot", "dashscope", "groq", "ollama"}
BUNDLED = Path(__file__).with_name("data") / "prices.json"

# (input, output, cache_read, cache_write_5m, cache_write_1h) in USD per 1M tokens
Price = tuple


def _home() -> Path:
    import os

    return Path(os.environ.get("AI_TOKEN_TRACKER_DIR") or Path.home() / ".ai-token-tracker").expanduser()


def cache_file() -> Path:
    return _home() / "prices-cache.json"


def override_file() -> Path:
    return _home() / "prices.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _row(values: list) -> Price | None:
    if not isinstance(values, list) or len(values) < 2:
        return None
    vals = [v if isinstance(v, (int, float)) else None for v in values] + [None] * 5
    inp, out, read, w5, w1 = vals[:5]
    if inp is None or out is None:
        return None
    return (inp, out, read if read is not None else inp, w5 if w5 is not None else inp,
            w1 if w1 is not None else (w5 if w5 is not None else inp))


@lru_cache(maxsize=1)
def price_table() -> tuple[dict, dict]:
    """Returns (table, info). table: normalized model id -> Price."""
    table: dict[str, Price] = {}
    info = {"source": "bundled", "updated": None, "overrides": 0}
    for layer, path in (("bundled", BUNDLED), ("online", cache_file())):
        doc = _read(path)
        models = doc.get("models") if isinstance(doc.get("models"), dict) else {}
        for name, values in models.items():
            row = _row(values)
            if row:
                table[normalize(name)] = row
        if models:
            info["source"], info["updated"] = layer, doc.get("fetched")
    for name, p in _read(override_file()).items():
        if isinstance(p, dict) and isinstance(p.get("input"), (int, float)):
            w = p.get("cache_write", p["input"])
            table[normalize(name)] = (p["input"], p.get("output", 0), p.get("cache_read", p["input"]), w,
                                      p.get("cache_write_1h", w))
            info["overrides"] += 1
    return table, info


def reload() -> None:
    price_table.cache_clear()
    lookup.cache_clear()


def normalize(model: str) -> str:
    m = model.strip().lower()
    m = re.sub(r"\[.*?\]$", "", m)          # claude-opus-5-5[1m]
    m = re.sub(r"@\d{8}$", "", m)           # vertex: claude-x@20250929
    m = re.sub(r"-\d{8}$", "", m)           # dated snapshot: claude-x-20250929
    m = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", m)  # openai: gpt-x-2025-08-07
    return m


@lru_cache(maxsize=4096)
def lookup(model: str) -> Price | None:
    """Best price row for a model id as the tools write it, or None."""
    if not model:
        return None
    table, _ = price_table()
    m = normalize(model)
    candidates = [m, m.split("/")[-1]]  # "openai/gpt-5" -> "gpt-5"
    candidates += [f"{p}/{m.split('/')[-1]}" for p in ("gemini", "xai", "deepseek", "mistral", "moonshot", "dashscope", "ollama")]
    for c in candidates:
        if c in table:
            return table[c]
    # Longest known id that the model starts with: "gpt-5-codex-high" -> "gpt-5-codex"
    bare = m.split("/")[-1]
    best = max((k for k in table if bare.startswith(k.split("/")[-1] + "-")), key=len, default=None)
    return table[best] if best else None


def cost(model: str, usage: Usage) -> float | None:
    """Estimated USD for one call, or None when the model has no known price."""
    p = lookup(model)
    if p is None:
        return None
    inp, out, read, w5, w1 = p
    one_hour = min(usage.cache_write_1h, usage.cache_write)
    return (usage.input * inp + usage.output * out + usage.cache_read * read
            + (usage.cache_write - one_hour) * w5 + one_hour * w1) / 1_000_000


def apply_costs(sessions) -> None:
    """Fill usage.cost / usage.unpriced on every API call."""
    for s in sessions:
        for call in s.calls:
            c = cost(call.model, call.usage)
            if c is None:
                call.usage.cost, call.usage.unpriced = 0.0, call.usage.total
            else:
                call.usage.cost, call.usage.unpriced = c, 0


def trim(litellm: dict) -> dict:
    """Keep chat models from the main providers, in our compact row format."""
    def per_m(x):
        return round(x * 1e6, 6) if isinstance(x, (int, float)) else None

    out = {}
    for name, v in litellm.items():
        if not isinstance(v, dict) or v.get("litellm_provider") not in PROVIDERS:
            continue
        if v.get("mode") not in ("chat", "responses"):
            continue
        row = [per_m(v.get(k)) for k in ("input_cost_per_token", "output_cost_per_token",
                                         "cache_read_input_token_cost", "cache_creation_input_token_cost",
                                         "cache_creation_input_token_cost_above_1hr")]
        if row[0] is None or row[1] is None:
            continue
        while row[-1] is None:
            row.pop()
        out[name] = row
    return out


def update_prices(timeout: float = 30) -> int:
    """Download the latest LiteLLM price list into the local cache. Returns model count."""
    with urllib.request.urlopen(LITELLM_URL, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        data = json.loads(resp.read().decode("utf-8"))
    models = trim(data)
    if len(models) < 50:
        raise ValueError("price list looks incomplete; keeping the current prices")
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"source": LITELLM_URL, "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "unit": "USD per 1M tokens", "models": models}
    path.write_text(json.dumps(doc, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    reload()
    return len(models)


def pricing_info() -> dict:
    table, info = price_table()
    return dict(info, models=len(table))
