"""Small process-local cache for on-demand EC2 confidence trends."""

from __future__ import annotations

import copy
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Callable, Hashable


class EC2TrendCache:
    def __init__(
        self,
        ttl_seconds: int = 3600,
        capacity: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self._clock = clock
        self._lock = Lock()
        self._entries: OrderedDict[
            Hashable, tuple[float, dict[str, Any]]
        ] = OrderedDict()

    def get(self, key: Hashable) -> dict[str, Any] | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return copy.deepcopy(value)

    def set(self, key: Hashable, value: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = (
                self._clock() + self.ttl_seconds,
                copy.deepcopy(value),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


ec2_trend_cache = EC2TrendCache()
