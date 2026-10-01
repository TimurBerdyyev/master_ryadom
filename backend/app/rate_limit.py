import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request, status

_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def rate_limit(prefix: str, max_attempts: int, window_seconds: int):
    def dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        key = f"{prefix}:{client_ip}"
        now = time.monotonic()
        with _lock:
            attempts = _attempts[key]
            cutoff = now - window_seconds
            while attempts and attempts[0] < cutoff:
                attempts.pop(0)
            if len(attempts) >= max_attempts:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Слишком много попыток, попробуйте позже",
                )
            attempts.append(now)

    return dependency
