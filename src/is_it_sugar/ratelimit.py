"""Generous per-IP limits on checks. In memory, so a restart resets them."""

import time
from collections import defaultdict, deque
from collections.abc import Sequence


class RateLimiter:
    def __init__(self, limits: Sequence[tuple[int, float]]):
        """`limits` is a list of (max requests, window in seconds)."""
        self.limits = sorted(limits, key=lambda lw: lw[1])
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._longest = self.limits[-1][1]

    def hit(self, ip: str) -> tuple[bool, int, float]:
        """Record a request. Returns (allowed, remaining in the tightest window, seconds until retry)."""
        now = time.monotonic()
        hits = self._hits[ip]
        while hits and hits[0] <= now - self._longest:
            hits.popleft()
        if len(self._hits) > 10_000:
            self._prune(now)

        remaining = []
        for limit, window in self.limits:
            in_window = [t for t in hits if t > now - window]
            if len(in_window) >= limit:
                return False, 0, in_window[0] + window - now
            remaining.append(limit - len(in_window) - 1)
        hits.append(now)
        return True, min(remaining), 0.0

    def _prune(self, now: float) -> None:
        for ip in [ip for ip, h in self._hits.items() if not h or h[-1] <= now - self._longest]:
            del self._hits[ip]
