"""Per-IP attempt limits (login, sign-up, SMS codes, complaints).

Uses Redis when REDIS_URL is reachable, so limits hold across several backend processes/containers;
otherwise falls back to process memory (fine for a single process and for local development).
"""
import logging
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request, status

from app.config import settings

logger = logging.getLogger("master_ryadom")

_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()
_redis = None
_redis_checked = False


def _get_redis():
    """Connect once; on failure use memory for the rest of the process life."""
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    if not settings.redis_url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        _redis = client
        logger.info("Лимиты попыток хранятся в Redis")
    except Exception:
        logger.warning("Redis недоступен (%s) — лимиты попыток хранятся в памяти процесса", settings.redis_url)
    return _redis


def _hit_redis(client, key: str, max_attempts: int, window_seconds: int) -> bool:
    """Sliding window in a sorted set; True if this attempt is allowed."""
    now = time.time()
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_seconds)
    pipe.zcard(key)
    count = pipe.execute()[1]
    if count >= max_attempts:
        return False
    pipe = client.pipeline()
    pipe.zadd(key, {f"{now}": now})
    pipe.expire(key, window_seconds)
    pipe.execute()
    return True


def _hit_memory(key: str, max_attempts: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _lock:
        attempts = _attempts[key]
        cutoff = now - window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.pop(0)
        if len(attempts) >= max_attempts:
            return False
        attempts.append(now)
        return True


def client_ip(request: Request) -> str:
    """The real client address for per-IP limits (see settings.client_ip_header)."""
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header, "").split(",")[0].strip()
        if value:
            return value[:64]
    return request.client.host if request.client else "unknown"


def rate_limit(prefix: str, max_attempts: int, window_seconds: int):
    def dependency(request: Request) -> None:
        ip = client_ip(request)
        key = f"rl:{prefix}:{ip}"
        client = _get_redis()
        try:
            allowed = _hit_redis(client, key, max_attempts, window_seconds) if client else \
                _hit_memory(key, max_attempts, window_seconds)
        except Exception:
            # Redis went away mid-flight: don't lock everyone out of logging in.
            allowed = _hit_memory(key, max_attempts, window_seconds)
        if not allowed:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток, попробуйте позже")

    return dependency
