"""FastAPI application entry point: wiring, error handlers, table creation."""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .core.errors import APIError, api_error_handler
from .database import Base, engine
from .routers import auth, orders, products

# WHY create_all (not Alembic) for dev: zero-setup — tables appear on first run so the
# app is immediately usable. The schema is identical to what Alembic would generate; for
# production you swap to Alembic migrations (documented in the README). Code unchanged.
Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Order Management System", version="1.0.0")

api_prefix = f"/{settings.api_version}"  # /v1 — versioning from day one


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
