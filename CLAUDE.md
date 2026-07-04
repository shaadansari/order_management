# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI backend for an e-commerce **order management system**. Two roles: **customers**
(register, browse, order, pay, cancel) and **admins** (manage their own products, view all
orders). Auth is JWT (`Bearer` token). All routes are versioned under `/v1`.

`order-management-design.md` (repo root) is the original design rationale; the README lists
where the implementation deliberately diverges from it. Read both before non-trivial design changes.

## Commands

```bash
pip install -r requirements.txt        # install deps (use a virtualenv)
uvicorn app.main:app --reload          # dev server — tables auto-create on first start
pytest -v                              # full suite (isolated in-memory SQLite per test)
pytest tests/test_orders.py -v         # one file
pytest tests/test_orders.py::test_name # single test
# Interactive API docs while the server runs: http://127.0.0.1:8000/docs
```

There is no linter/formatter configured. `Dockerfile` containerizes the app
(`docker build -t oms . && docker run -p 8000:8000 oms`).

## Architecture: strict layering

Requests flow **routers → services → models**, and the boundary is enforced by convention:

- `routers/` — HTTP in/out *only*. Parse request, call a service function, serialize the
  ORM result into a response schema. No business logic here.
- `services/` — all business logic. Pure functions taking a `Session` (+ ids), returning ORM
  objects. They raise `APIError` on failure and `commit` themselves.
- `models/` — SQLAlchemy ORM = what the DB stores. `schemas/` = what the API accepts/returns.
- `core/` — leaf utilities (`errors.py`, `security.py`). `middleware/` = FastAPI `Depends`
  auth gatekeepers (not ASGI middleware). `workers/` = background tasks.

Two routers per resource where roles split: e.g. `orders.py` exposes both
`customer_router` and `admin_orders_router`; `products.py` exposes public `router` and
`admin_products_router`. `main.py` mounts each under `/v1`.

## Critical conventions (read before editing these areas)

**Single error contract.** Every failure returns `{"error": <CODE>, "message": <...>, "status": <http>}`.
Raise `APIError(status, CODE, message)` or a subclass (`NotFoundError`, `ForbiddenError`,
`UnauthorizedError`) from services/routers — `main.py`'s exception handlers normalize
everything (including FastAPI 422s and framework 404/405) into this shape. Never return
ad-hoc `JSONResponse`/`HTTPException` for errors.

**Stock reservation is the core correctness mechanism** (`services/order_service.create_order`,
delegated to the `_reserve_stock_atomic` helper). Stock is decremented *atomically at order
creation*, not at payment. It uses a conditional bulk UPDATE —
`UPDATE product SET stock = stock - :qty WHERE id = :id AND stock >= :qty` — checking
`result.rowcount`; if 0, roll back the *entire* order. Do **not** replace this with
`SELECT ... FOR UPDATE`: SQLite ignores it and gives false safety. The conditional-UPDATE
pattern is race-free on both SQLite and Postgres. `cancel_order` *restores* stock symmetrically.

**Money.** `Numeric(10,2)` everywhere (never float). Order totals are computed **server-side**
from current DB prices — never trust a client-sent total. `OrderItem.unit_price` is a **price
snapshot** taken at order time, not a live join to `product.price` (historical orders must not
change when prices do).

**Background tasks** (`workers/invoice.py`, fired by `pay_order`): they run *after* the
response is sent, so the request's DB session is already closed. Pass **primitive IDs only**
(never detached ORM objects) and let the worker open its own `SessionLocal()`. A background task
must never crash the app — catch and log.

**Session/identity-map gotcha:** `create_order`/`cancel_order` mutate stock with bulk UPDATEs
that bypass SQLAlchemy's identity map, so cached `Product` objects show stale stock. Order
responses are therefore built via `_load_order_for_response` (and the list queries), which
eager-load `Order.items → OrderItem.product` with `.populate_existing()` to force a fresh read.
The serializers (`to_customer_order_out`, `to_admin_order_out`, `_item_to_out`) are **pure
mappers** (no `db`) and assume the caller already eager-loaded those relations.

**Comment style:** this codebase is densely annotated with `WHY` comments explaining each
non-obvious decision. Match that style and update (don't delete) the relevant `WHY` when
changing the behavior it documents.

## Concurrency / DB setup

`database.py` enables SQLite WAL + `foreign_keys=ON` on every connection, and sets
`expire_on_commit=False` (so background tasks and post-commit code can still read objects).
For production, switch `DATABASE_URL` to Postgres — app code is unchanged. Dev uses
`Base.metadata.create_all` on startup; production should adopt Alembic (schema is identical).

## Testing patterns

`tests/conftest.py` gives each test an isolated **in-memory SQLite** DB (`StaticPool` = one
shared connection per test) and overrides the `get_db` dependency so every request reuses the
test's session. Fixtures `admin_token` / `customer_token` register+login; `make_product(...)`
creates a product as admin. Auth is exercised via the `Authorization: Bearer <jwt>` header.

**Testing hooks built into the API:** `POST /v1/orders/{id}/pay?force_fail=true` forces a 402
declined-payment path (`order_service._simulate_payment_gateway` is the stub to replace for a
real provider).

## Notes

- `.env` (gitignored) holds config; values are read once into `app/config.py`'s `Settings`. No
  scattered `os.getenv`. `API_VERSION` controls the `/v1` prefix.
- `created_by` ties each product to its admin; admins can only mutate *their own* products
  (`product_service._get_owned_product` enforces this → 403 for others).
- `GET /admin/products` shows an admin's products *including soft-deleted* ones; public
  endpoints hide soft-deleted (`is_available=false`) products.
