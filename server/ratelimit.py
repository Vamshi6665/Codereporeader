"""
ratelimit.py
============
Fixed-window rate limiting per client IP.

* If Upstash Redis (or Vercel KV) REST credentials are present, counters are shared
  across every function instance, which makes the limits exact.
* Otherwise counters live in the instance's memory. With Fluid compute an instance
  serves many requests, so this still blunts abuse; pair it with a Vercel WAF
  rate-limit rule for hard protection.

The limiter fails open: if Redis is unreachable, requests are allowed rather than
locking every user out.
"""

from __future__ import annotations

import os
import threading
import time

import requests

_URL = (os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL") or "").strip().rstrip("/")
_TOKEN = (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN") or "").strip()

_mem: dict = {}
_lock = threading.Lock()


def backend() -> str:
    return "redis" if (_URL and _TOKEN) else "memory"


def _redis_incr(key: str, window_s: int):
    r = requests.post(
        f"{_URL}/pipeline",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json=[["INCR", key], ["EXPIRE", key, str(window_s + 5)]],
        timeout=1.5,
    )
    r.raise_for_status()
    return int(r.json()[0]["result"])


def _mem_incr(key: str, window_s: int):
    now = time.time()
    with _lock:
        if len(_mem) > 50_000:  # drop expired keys occasionally
            for k in [k for k, (_, exp) in _mem.items() if exp < now]:
                _mem.pop(k, None)
        count, exp = _mem.get(key, (0, now + window_s))
        if exp < now:
            count, exp = 0, now + window_s
        count += 1
        _mem[key] = (count, exp)
        return count


def hit(bucket: str, ident: str, limit: int, window_s: int):
    """Count one request. Returns (allowed, retry_after_seconds)."""
    if limit <= 0:
        return True, 0
    window_id = int(time.time() // window_s)
    key = f"crr:rl:{bucket}:{ident}:{window_id}"
    try:
        count = _redis_incr(key, window_s) if backend() == "redis" else _mem_incr(key, window_s)
    except Exception:  # noqa: BLE001 — fail open
        count = _mem_incr(key, window_s)
    if count > limit:
        retry = int(window_s - (time.time() % window_s)) + 1
        return False, retry
    return True, 0


def limits_from_env():
    def n(name, default):
        try:
            return int(os.environ.get(name, "").strip() or default)
        except ValueError:
            return default
    return {
        "chat_per_minute": n("CHAT_LIMIT_PER_MINUTE", 12),
        "chat_per_day": n("CHAT_LIMIT_PER_DAY", 300),
        "analyze_per_10min": n("ANALYZE_LIMIT_PER_10MIN", 20),
        "global_chat_per_day": n("GLOBAL_CHAT_LIMIT_PER_DAY", 0),   # 0 = off (needs Redis to be exact)
    }
