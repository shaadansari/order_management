"""Rate limiting (slowapi) — brute-force protection on the auth endpoints.

WHY a dedicated module (not main.py): the auth router imports this `limiter` singleton to
decorate login/register with @limiter.limit(...). Importing it from main.py would create a
circular import (main imports the routers, the auth router would import main), so the
instance lives here and both main.py (wiring) and the auth router (decorators) import it.

WHY in-memory storage (the default): fine for a single-process dev app. For multi-worker
production, point slowapi at Redis via `storage_uri="redis://..."` so counters are shared
across workers — otherwise each worker enforces its own independent limit.

WHY headers_enabled: expose X-RateLimit-Limit / X-RateLimit-Remaining so well-behaved
clients can see their budget and back off before being refused.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# WHY get_remote_address: bucket limits per client IP — the right granularity for throttling
# brute-force attempts against /auth/login and /auth/register.
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
