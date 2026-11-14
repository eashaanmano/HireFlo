"""In-memory sliding-window rate limiter (per process). Use Redis-backed limits for multi-instance deploys."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_sec: float) -> bool:
        """Record a hit; return False if the key exceeded `limit` within `window_sec`."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_sec:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, limit: int, window_sec: float) -> Callable[[Request], None]:
    """FastAPI dependency factory: `Depends(rate_limit("otp", 10, 600))`."""

    def dependency(request: Request) -> None:
        if not limiter.hit(f"{name}:{client_ip(request)}", limit, window_sec):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Try again shortly.")

    return dependency
