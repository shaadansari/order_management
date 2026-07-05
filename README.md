# Order Management System

A FastAPI backend for a simple e-commerce order management system. Two user types:
**customers** (register, browse, order, pay, cancel) and **admins** (manage products,
view all orders).

See [`order-management-design.md`](./order-management-design.md) for the full design
rationale. This README covers running it.

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

## Run the tests

```bash
pytest -v
```

Tests use an isolated in-memory SQLite database per test, so they don't touch your dev DB.

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

## Key design decisions (and where they differ from the doc)

These were reviewed against the design doc and adjusted for correctness. Each is also
documented inline in the code.

1. **Stock is reduced only on successful payment, not at order creation.** An order is
   just a CREATED intent with a price snapshot; it reserves nothing. The decrement happens
   in `pay_order` via an atomic conditional
   `UPDATE product SET stock = stock - :qty WHERE id = :id AND stock >= :qty` checked via
   `rowcount` — race-free inventory management on both SQLite and PostgreSQL. If any item
   can't be fulfilled, the whole payment rolls back (no stock lost, no charge).
2. **Atomic conditional `UPDATE` instead of `SELECT ... FOR UPDATE`.** SQLite ignores
   `FOR UPDATE` (no row locks), so it gives false safety in dev. The pattern above (checked
   via `rowcount`) is race-free on **both** SQLite and PostgreSQL.
3. **`Numeric(10,2)` for money instead of `REAL`** (float) — avoids float drift like
   `0.1 + 0.2 != 0.3`. Maps to `DECIMAL` on Postgres unchanged.
4. **Cancel does not restore stock.** Because stock is only ever reduced on successful
   payment, a CREATED order holds no inventory — there is nothing to give back. Cancelling
   just flips the status to CANCELLED (the order + items are kept for history).
5. **`create_all` for dev, Alembic for prod.** Tables auto-create on startup for zero
   setup. Switch to Alembic migrations for production (schema is identical).

Everything else (bcrypt, JWT, `/v1` versioning, soft delete, price snapshot, server-side
totals, consistent error shape, 401-vs-403 semantics, services layer) is implemented as
designed.

---

## Error format

Every error returns the same shape:

```json
{ "error": "INSUFFICIENT_STOCK", "message": "Insufficient stock for product 'Laptop'", "status": 400 }
```

## Production checklist (documented, not wired)

- **DB:** swap `DATABASE_URL` to PostgreSQL — code unchanged.
- **Migrations:** adopt Alembic (replace `create_all`).
- **Caching:** Redis in front of `GET /v1/products` (TTL ~5 min), invalidate on write.
- **Rate limiting:** throttle `/v1/auth/login` (e.g. 5/min) against brute force.
- **Background jobs:** replace `BackgroundTasks` with Celery + Redis/broker for retries
  and durability; add an idempotency key on `POST /orders/{id}/pay` to dedupe payments.
- **Real payment gateway:** use two-phase **authorize → reserve stock → capture**. The
  simulated single-call flow holds the product row lock across the charge; with a real
  (slow) gateway that risks lock contention and a charge that succeeds but fails to commit.
  Two-phase authorizes funds first (reversible), reserves stock, then captures — so a
  failure after authorization can be voided without losing funds.
- **Abandoned orders:** scheduled job to flag/remove stale `CREATED` orders after 15 days.
- **Observability:** Sentry (errors) + Prometheus (metrics); structured logging.
- **CI/CD:** GitHub Actions; the included `Dockerfile` containerizes the app.



models/      → what DB stores
schemas/     → what API shows
routers/     → HTTP in/out only
services/    → business logic
core/        → shared tools (errors, security)
middleware/  → request gatekeepers (auth)
workers/     → background jobs
migrations/  → DB change history