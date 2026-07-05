"""FastAPI application entry point: wiring, error handlers, table creation."""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .core.errors import APIError, api_error_handler
from .core.limiter import limiter
from .database import Base, engine
from .routers import auth, orders, products

logger = logging.getLogger(__name__)

# WHY conditional create_all: development/test gets zero-config setup (tables appear on
# first run). Production must NOT auto-create — that would bypass Alembic's versioning and
# could mask an out-of-date schema; start.sh runs `alembic upgrade head` before the server
# boots instead. create_all is idempotent, so running it in dev alongside an Alembic-
# migrated DB is harmless (tables already exist -> no-op).
if settings.app_env in ("development", "test"):
    Base.metadata.create_all(bind=engine)
else:
    logger.info("Production mode: skipping create_all — Alembic handles migrations")

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Order Management System", version="1.0.0")

api_prefix = f"/{settings.api_version}"  # /v1 — versioning from day one

# Rate limiting (slowapi). WHY the limiter instance lives in app.core.limiter: the auth
# router imports it to decorate login/register, so defining it in main would be a circular
# import (main imports the routers). The middleware reads the limiter off app.state.
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# WHY permissive CORS: browser frontends on any origin must be able to call this API.
# Registered after SlowAPIMiddleware so it is the outermost layer (last added = outermost in
# Starlette) and handles OPTIONS preflight before rate limiting/routing. allow_credentials is
# left False (the default): the API authenticates via a Bearer token in the Authorization
# header, NOT cookies, so no credentialed cross-origin requests are needed — and the CORS
# spec forbids combining allow_origins=["*"] with allow_credentials=True (browsers reject it).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Error handlers — normalize EVERY failure into the single error contract shape.
# --------------------------------------------------------------------------- #
@app.exception_handler(APIError)
async def _handle_api_error(request: Request, exc: APIError):
    return await api_error_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _handle_starlette(_: Request, exc: StarletteHTTPException):
    # Map framework errors (e.g. 404 unknown route, 405 wrong method) into our shape.
    code_map = {
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
    }
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": code_map.get(exc.status_code, "HTTP_ERROR"),
            "message": str(exc.detail),
            "status": exc.status_code,
        },
    )


@app.exception_handler(RequestValidationError)
async def _handle_validation(_: Request, exc: RequestValidationError):
    # WHY wrap 422s: the design says every error shares one shape. Raw FastAPI 422 bodies
    # differ, so we adapt while preserving the field-level details under "details".
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "status": 422,
            "details": jsonable_encoder(exc.errors()),
        },
    )


@app.exception_handler(RateLimitExceeded)
async def _handle_rate_limit(_: Request, _exc: RateLimitExceeded):
    # Map slowapi's RateLimitExceeded into the standard error shape so even 429s share the
    # one contract clients expect.
    return JSONResponse(
        status_code=429,
        content={"error": "RATE_LIMIT_EXCEEDED", "message": "Too many requests", "status": 429},
    )


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #
app.include_router(auth.router, prefix=api_prefix)
app.include_router(products.router, prefix=api_prefix)
app.include_router(products.admin_products_router, prefix=api_prefix)
app.include_router(orders.customer_router, prefix=api_prefix)
app.include_router(orders.admin_orders_router, prefix=api_prefix)
