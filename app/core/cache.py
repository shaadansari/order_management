"""Redis cache with graceful degradation.

WHY a thin wrapper: centralizes key construction, TTL, and the degrade-on-failure rule so
callers (product_service) stay simple. If Redis is down, every method no-ops / returns None
and the app keeps serving from the DB — just slower. Nothing in the request path ever raises
because of Redis.
"""
import logging

import redis
from redis.exceptions import RedisError

from ..config import settings

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis-backed cache. All methods are safe to call when Redis is unreachable."""

    def __init__(self, url: str | None = None):
        # WHY from_url + decode_responses: lets REDIS_URL drive every aspect (host/port/db/
        # password) and makes get() return str instead of bytes, so callers don't decode.
        # redis-py connects lazily on the first command, so constructing the client never
        # blocks or raises even if Redis is down — the first get/set catches the error.
        self._client = redis.Redis.from_url(
            url or settings.redis_url,
            decode_responses=True,
        )

    def get(self, key: str) -> str | None:
        try:
            return self._client.get(key)
        except RedisError as e:
            # Broader than ConnectionError: also covers timeout / auth / busy-loader. Treat
            # any failure as a cache miss so the caller falls back to the DB. Include `e` so
            # the cause (refused / timeout / auth) is visible in the log.
            logger.warning("cache get failed for %r — serving from DB (%s)", key, e)
            return None

    def set(self, key: str, value: str, ttl: int) -> None:
        try:
            self._client.set(key, value, ex=ttl)
        except RedisError as e:
            logger.warning("cache set failed for %r — value not stored (%s)", key, e)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except RedisError as e:
            logger.warning("cache delete failed for %r (%s)", key, e)

    def delete_pattern(self, pattern: str) -> None:
        # WHY SCAN (not KEYS): KEYS blocks the Redis server while it scans the whole keyspace;
        # SCAN is cursor-based and non-blocking, safe for production. Collect matches then
        # delete in one round trip.
        try:
            keys = list(self._client.scan_iter(pattern))
            if keys:
                self._client.delete(*keys)
        except RedisError as e:
            logger.warning("cache delete_pattern failed for %r (%s)", pattern, e)


# Single shared instance imported across the app.
cache = RedisCache()
