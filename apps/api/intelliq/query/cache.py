"""In-process result cache.

Keys are derived from the *rendered* SQL and bound parameter values, which
already include every row-level-security predicate for the requesting user,
plus the dataset revision. Two users only share an entry when they would
receive byte-identical queries — i.e. identical data entitlements.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import date, datetime
from typing import Any


def cache_key(*parts: Any) -> str:
    def default(o: Any) -> str:
        if isinstance(o, datetime | date):
            return o.isoformat()
        return repr(o)

    raw = json.dumps(parts, default=default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ResultCache:
    def __init__(self, max_entries: int = 512, max_cells: int = 5_000_000):
        self.max_entries = max_entries
        self.max_cells = max_cells
        self._data: OrderedDict[str, tuple[float, int, Any]] = OrderedDict()
        self._cells = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires, _, value = item
            if expires < time.monotonic():
                self._evict(key)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: Any, ttl_seconds: float, cells: int) -> None:
        if ttl_seconds <= 0 or cells > self.max_cells // 4:
            return
        with self._lock:
            if key in self._data:
                self._evict(key)
            self._data[key] = (time.monotonic() + ttl_seconds, cells, value)
            self._cells += cells
            while len(self._data) > self.max_entries or self._cells > self.max_cells:
                self._evict(next(iter(self._data)))

    def _evict(self, key: str) -> None:
        _, cells, _ = self._data.pop(key)
        self._cells -= cells

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._cells = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._data), "cells": self._cells, "hits": self.hits, "misses": self.misses}
