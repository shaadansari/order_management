# Order Management System

A FastAPI backend for a simple e-commerce order management system. Two user types:
**customers** (register, browse, order, pay, cancel) and **admins** (manage products,
view all orders).

See [Features](#features) for what's included; the sections below cover running it.

---

## Features

- **Role-based routing** — Each resource exposes separate customer/admin routers guarded by `require_customer` / `require_admin`, so a customer can never reach an admin endpoint (or vice-versa).
- **API versioning** — Every route is mounted under `/v1` (driven by `API_VERSION`), so breaking changes can ship under a new prefix without disturbing existing clients.
- **Stateless JWT auth (serverless-friendly)** — Bearer-token authentication with no server-side session store, so it scales horizontally across workers/instances with zero shared state.
- **Type-safe API** — Pydantic v2 schemas validate every request and response, and money uses `Numeric(10,2)` to avoid float drift — bad input is rejected before it reaches business logic.
- **Single error contract** — Every failure returns `{"error", "message", "status"}` (422s, 404s, and 429s included), so clients parse one predictable shape.
- **Race-condition-safe stock** — Payment decrements stock with a conditional `UPDATE ... WHERE stock >= qty` checked via `rowcount`, so two customers can't oversell the last item (works on SQLite *and* Postgres).
- **Duplicate-operation guards** — The order state machine rejects invalid transitions (`ORDER_ALREADY_PAID`, `ORDER_NOT_PAYABLE`, `ORDER_NOT_CANCELLABLE`), so a request can't double-charge or double-cancel.
- **Redis caching (graceful degradation)** — Product lists are cached in Redis (5-min TTL, invalidated on writes); if Redis is down the app silently serves from the DB and never fails a request.
- **Celery background tasks** — Invoice generation, order notifications, and low-stock alerts run in Celery workers (Redis broker) with automatic retries, so slow/external work never blocks the API response.
- **Rate limiting** — slowapi throttles `/auth/login` (5/min) and `/auth/register` (3/min) per IP to blunt brute-force and signup spam.
- **CORS enabled** — Permissive cross-origin access so any browser frontend can call the API.
- **Database migrations + PostgreSQL** — Alembic versioned migrations; switch from SQLite (dev) to PostgreSQL (prod) by changing one environment variable.
- **CI/CD pipeline** — GitHub Actions runs `ruff` lint → tests with a coverage gate → Docker build smoke test → Render deploy on push to `main`.
- **Dockerized** — Multi-stage `Dockerfile` (non-root user, health check) plus `docker-compose` for the full local stack (API + Redis + Celery worker + Flower).
- **Linting with Ruff** — `ruff check` enforces a consistent style and is a hard CI gate.
- **Test coverage** — A pytest suite (isolated in-memory SQLite per test, with fakes for Redis and Celery) keeps tests hermetic; coverage is enforced in CI.

---

## Quick start

```bash
# 1. Create + activate a virtualenv (optional but recommended)
python -m venv .venv
#   Windows:  .venv\Scripts\activate
#   macOS/Linux: source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env        # then edit JWT_SECRET_KEY for any real use

# 4. Run the dev server (tables auto-create on first start)
uvicorn app.main:app --reload

# 5. Open the interactive docs
#    http://127.0.0.1:8000/docs
```

### Using Docker

```bash
# Full local stack — API (:8000) + Redis + Celery worker + Flower dashboard (:5555)
docker compose up

# …or run just the API in a single container
docker build -t oms .
docker run -p 8000:8000 oms
```

Then open http://127.0.0.1:8000/docs (Flower dashboard at http://localhost:5555 when using `docker compose up`).

## Run the tests

```bash
pytest -v
```

Tests use an isolated in-memory SQLite database per test, so they don't touch your dev DB.

---

## Database migrations

| Environment | Database   | Schema setup |
|-------------|------------|--------------|
| Development | SQLite     | Auto via `create_all` on startup |
| Testing     | SQLite     | Auto via `create_all` per test run (in-memory DB) |
| Production  | PostgreSQL | `alembic upgrade head`, run by `start.sh` before the server boots |

Common commands:

```bash
alembic upgrade head                       # apply all pending migrations
alembic current                            # show the active migration version
alembic history                            # full migration history
alembic revision --autogenerate -m "..."   # create a migration after changing models
alembic downgrade -1                       # roll back one migration
```

Switching to PostgreSQL:

1. Set `DATABASE_URL=postgresql://user:password@host:5432/dbname` in the environment.
2. Run `alembic upgrade head` (or set `APP_ENV=production` and let `start.sh` run it on boot).
3. No code changes — the engine branches on the URL scheme (see `app/database.py`).

> After changing a model, run `alembic revision --autogenerate -m "..."`, **review the
> generated file** (autogenerate isn't always right — e.g. it can't detect renames), then
> `alembic upgrade head`. On SQLite, column changes go through Alembic batch mode
> (`render_as_batch` in `migrations/env.py`) because SQLite can't `ALTER` most things in place.

---

## API summary (all under `/v1`)

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/v1/auth/register` | public | Register (email, password, role) |
| POST | `/v1/auth/login` | public | Login → JWT |
| GET | `/v1/products` | public | List + search + paginate |
| GET | `/v1/products/{id}` | public | Single product |
| POST | `/v1/products` | admin | Create product |
| PUT | `/v1/products/{id}` | admin | Update product |
| DELETE | `/v1/products/{id}` | admin | Soft delete (`is_available=false`) |
| GET | `/v1/admin/products` | admin | List own products |
| POST | `/v1/orders` | customer | Create order (status `CREATED`) |
| POST | `/v1/orders/{id}/pay` | customer | Pay order → `PAID` (stock reduced here) |
| POST | `/v1/orders/{id}/cancel` | customer | Cancel order → `CANCELLED` |
| GET | `/v1/orders` | customer | Own order history |
| GET | `/v1/admin/orders` | admin | All orders (filter by `?status=`) |

Send the JWT as `Authorization: Bearer <token>`.

**Testing hook:** `POST /v1/orders/{id}/pay?force_fail=true` simulates a declined payment
to exercise the 402 path.

---

## Error format

Every error returns the same shape:

```json
{ "error": "INSUFFICIENT_STOCK", "message": "Insufficient stock for product 'Laptop'", "status": 400 }
```

## Project structure

models/      → what DB stores
schemas/     → what API shows
routers/     → HTTP in/out only
services/    → business logic
core/        → shared tools (errors, security)
middleware/  → request gatekeepers (auth)
workers/     → background jobs
migrations/  → DB change history