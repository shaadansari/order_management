
# Order Management System — Complete Design Document

> This document captures all architecture decisions, DB design, API design, edge cases, tradeoffs, and production considerations discussed before implementation.

---

## 1. System Overview

A backend service for a simple e-commerce order management system.

**Two user types:**
- **Customer** — register, login, view products, create orders, view their orders
- **Admin** — create/update products, view all orders

**Tech Stack Decision:**

| What | Tool | Why |
|---|---|---|
| Framework | FastAPI | Lightweight like Express, built-in validation (Pydantic), auto Swagger docs, async support |
| Database | SQLite (dev) | Zero config, acceptable per assignment, SQLAlchemy abstracts DB so swapping to PostgreSQL is just a connection string change |
| ORM | SQLAlchemy | Python's most mature ORM, like TypeORM/Prisma in Node world |
| Migrations | Alembic | Standard Python migration tool, pairs with SQLAlchemy |
| Auth | JWT (PyJWT) | Stateless, enables horizontal scaling, no server-side sessions |
| Password hashing | bcrypt | Industry standard, never store plain passwords |
| Background jobs | FastAPI BackgroundTasks | Built-in, no extra setup — good for invoice generation at this scope |
| Tests | pytest | Python's standard test runner |
| Docs | Auto-generated | FastAPI generates Swagger at /docs automatically |

---

## 2. Database Design

### Schema

```sql
CREATE TABLE IF NOT EXISTS "user" (
    "id"         INTEGER NOT NULL UNIQUE,
    "email"      VARCHAR NOT NULL UNIQUE CHECK(email LIKE '%_@__%.__%'),
    "password"   TEXT NOT NULL,
    "role"       TEXT DEFAULT 'customer' CHECK(role IN ('customer', 'admin')),
    "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY("id")
);

CREATE TABLE IF NOT EXISTS "product" (
    "id"           INTEGER NOT NULL,
    "created_by"   INTEGER NOT NULL,
    "name"         TEXT NOT NULL,
    "description"  TEXT,
    "price"        REAL NOT NULL DEFAULT 0,
    "is_available" BOOLEAN DEFAULT true,
    "stock"        INTEGER NOT NULL DEFAULT 0,
    "created_at"   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY("id"),
    FOREIGN KEY ("created_by") REFERENCES "user"("id")
    ON UPDATE NO ACTION ON DELETE NO ACTION
);

CREATE TABLE IF NOT EXISTS "order" (
    "id"           INTEGER NOT NULL,
    "user_id"      INTEGER NOT NULL,
    "status"       TEXT NOT NULL CHECK(status IN ('CREATED', 'PAID', 'CANCELLED')),
    "total_amount" REAL NOT NULL,
    "created_at"   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY("id"),
    FOREIGN KEY ("user_id") REFERENCES "user"("id")
    ON UPDATE NO ACTION ON DELETE NO ACTION
);

CREATE TABLE IF NOT EXISTS "order_items" (
    "id"         INTEGER NOT NULL,
    "order_id"   INTEGER NOT NULL,
    "product_id" INTEGER NOT NULL,
    "quantity"   INTEGER NOT NULL DEFAULT 1,
    "unit_price" REAL NOT NULL,
    PRIMARY KEY("id"),
    FOREIGN KEY ("order_id") REFERENCES "order"("id")
    ON UPDATE NO ACTION ON DELETE NO ACTION,
    FOREIGN KEY ("product_id") REFERENCES "product"("id")
    ON UPDATE NO ACTION ON DELETE NO ACTION
);
```

### Table Relationships

```
user (1) ──────────────── (many) order
user (1) ──────────────── (many) product  [created_by]
order (1) ─────────────── (many) order_items
product (1) ───────────── (many) order_items
```

### Key Design Decisions & Why

**`price` and `total_amount` as REAL not INTEGER**
Prices have decimals (12.99, 199.50). INTEGER would lose precision — real production bug.

**`unit_price` on order_items (price snapshot)**
Product prices change over time. Storing the price at the moment of order means historical orders always show the correct total — even if the product price changes later. This is how every real e-commerce system works.

**`is_available` boolean on product (soft delete)**
Never hard-delete products — old orders reference them. Setting `is_available = false` hides the product from customers while keeping data integrity and order history intact.

**`created_by` on product**
Links every product to the admin who created it. Enables admin to see only their own products, and provides audit trail.

**`name` on product — NOT UNIQUE**
Removed unique constraint — too strict. An admin may legitimately create "Premium Laptop" and "Basic Laptop".

**Email CHECK constraint at DB level**
Last line of defense after application validation. Pydantic validates first, DB constraint is the safety net.

**Role and status CHECK constraints**
Enforces valid values at the DB level — no invalid roles or statuses can ever be stored, even if app code has a bug.

---

## 3. API Design

### Auth

```
POST   /v1/auth/register    → Register new user (email, password, role)
POST   /v1/auth/login       → Login, returns JWT token
```

### Products

```
POST   /v1/products         → Admin: create product
GET    /v1/products         → Public: list products (search + pagination)
GET    /v1/products/{id}    → Public: single product detail
PUT    /v1/products/{id}    → Admin: update product
DELETE /v1/products/{id}    → Admin: soft delete (sets is_available = false)
GET    /v1/admin/products   → Admin: list their own products
```

### Orders

```
POST   /v1/orders               → Customer: create order (status: CREATED)
POST   /v1/orders/{id}/pay      → Customer: proceed to pay
POST   /v1/orders/{id}/cancel   → Customer: cancel order
GET    /v1/orders               → Customer: their own order history
GET    /v1/admin/orders         → Admin: all orders
```

### API Versioning — Why from Day One
All routes prefixed with `/v1/`. Future breaking changes go to `/v2/` without affecting existing clients. Zero extra effort now, prevents massive pain later.

---

## 4. Order Flow — The Core of the System

### Order Creation Flow (POST /v1/orders)

Per assignment specification, stock reduces on order creation:

```
Customer sends order request
        │
        ▼
Authenticate (JWT check)
        │
        ▼
Validate all products exist
        │
        ▼
BEGIN TRANSACTION
  → Validate stock >= requested quantity for each item
  → If any item fails → ROLLBACK → 400 "Insufficient stock"
  → Calculate total_amount (server side, never trust client)
  → Snapshot unit_price for each item
  → Reduce stock for each item
  → Create order record (status: CREATED)
  → Create order_items records
COMMIT
        │
        ▼
Return order confirmation
```

> **Production note:** In a real system, stock should only reduce on successful payment — not on order creation. This prevents stock being blocked by abandoned orders. For production, use the payment flow with row-level locking described below.

### Payment Flow (POST /v1/orders/{id}/pay) — Most Critical

```
Customer clicks "Proceed to Pay"
        │
        ▼
Authenticate (JWT check)
        │
        ▼
Is this customer's order? → No → 403 Forbidden
        │
        ▼
Is order status = CREATED?
→ Already PAID → 400 "Order already paid"
→ CANCELLED → 400 "Cannot pay cancelled order"
        │
        ▼
Customer selects payment method
        │
        ▼
BEGIN TRANSACTION
  → SELECT product FOR UPDATE  ← row-level lock (ACID Isolation)
  → Re-check stock >= quantity  ← second check under lock
  → If stock insufficient → ROLLBACK → 400 "Out of stock"
  → Simulate payment processing
  → If payment fails → ROLLBACK → 402 "Payment failed"
  → Update order status → PAID
COMMIT
        │
        ▼
Fire background tasks (non-blocking, API returns immediately)
  ├── Generate invoice
  ├── Send email notification
  └── (Production: RabbitMQ queue for reliability)
        │
        ▼
Return 200 — order confirmed
```

### Cancel Flow (POST /v1/orders/{id}/cancel)

```
Customer requests cancel
        │
        ▼
Is this customer's order? → No → 403
        │
        ▼
Is status = CREATED? → No → 400 "Can only cancel pending orders"
        │
        ▼
Update status → CANCELLED
        │
        ▼
Keep order in DB (admin can use for marketing reminders)
```

### Abandoned Order Policy
- CREATED orders not paid after **15 days** → flagged for marketing reminder
- After reminder period → auto-deleted via daily cron job
- Production: scheduled background job scans for stale CREATED orders daily

---

## 5. Race Condition — ACID & Isolation

**The problem:** Two customers simultaneously try to buy the last item in stock. Both check stock → both see `stock = 1` → both proceed → oversold.

**The solution — ACID Isolation + Row-Level Locking:**

```sql
BEGIN TRANSACTION;
SELECT * FROM product WHERE id = ? FOR UPDATE;  -- locks the row
-- only one request runs at a time from here
-- second request waits until first commits or rolls back
UPDATE product SET stock = stock - quantity WHERE id = ?;
COMMIT;
```

`SELECT FOR UPDATE` tells the DB: "I'm about to modify this row — don't let anyone else touch it until I'm done." The second request waits, then re-checks stock after the first commits. If stock is now 0 — it returns out of stock.

**Why this works:**
- User A locks the row
- User B waits
- User A reduces stock to 0, commits
- User B proceeds — sees stock = 0 — returns "out of stock"
- No overselling

---

## 6. Response Shapes

### Customer Order List Response

```json
{
  "order_id": 1,
  "status": "CREATED",
  "total_amount": 2400.00,
  "created_at": "2024-01-01T10:00:00",
  "items": [
    {
      "product_name": "Laptop",
      "quantity": 2,
      "unit_price": 1200.00,
      "current_stock": 8
    }
  ]
}
```

### Admin Order List Response

```json
{
  "order_id": 1,
  "status": "PAID",
  "total_amount": 2400.00,
  "created_at": "2024-01-01T10:00:00",
  "customer": {
    "id": 5,
    "email": "user@example.com"
  },
  "items": [
    {
      "product_name": "Laptop",
      "quantity": 2,
      "unit_price": 1200.00,
      "current_stock": 8
    }
  ]
}
```

### Consistent Error Response Shape

Every error in the API returns the same shape — no surprises for clients:

```json
{
  "error": "OUT_OF_STOCK",
  "message": "Product has insufficient stock",
  "status": 400
}
```

---

## 7. Security

**Implemented from day one:**

| Concern | Approach |
|---|---|
| Password storage | bcrypt hashing, never plain text |
| Authentication | JWT with expiry |
| Authorization | Role-based (customer vs admin), checked on every protected route |
| 401 vs 403 | 401 = not authenticated ("who are you?"), 403 = authenticated but not allowed ("I know you, but no") |
| Error messages | "Invalid credentials" — never "wrong password" (don't reveal which field failed) |
| Total price | Always calculated server-side, never trust client-sent total |
| Input validation | Pydantic validates all request bodies automatically |
| Secrets | JWT secret key in .env, never hardcoded |
| DB constraints | role, status, email enforced at DB level as last line of defense |

---

## 8. Scalability, Maintainability & Data Consistency

### What's Built In From Day One

**Scalability:**
- Stateless JWT auth → any request can hit any server instance → horizontal scaling ready
- API versioning (`/v1/`) → breaking changes go to `/v2/`, existing clients unaffected
- Pagination on all list endpoints → never return unbounded results

**Maintainability:**
- Clean project structure → routers / models / schemas / services / each has one job
- Services layer → business logic NEVER in routers, router only handles HTTP in/out
- Pydantic schemas separate from DB models → what DB stores vs what API exposes are different (e.g. DB stores hashed password, API never returns it)
- Logging on every important action → order created, payment succeeded/failed, stock updated
- Environment variables for all config → zero hardcoded values

**Data Consistency:**
- DB transactions on order creation and payment → atomic operations, no partial data ever
- unit_price snapshot → historical orders always show correct totals
- total_amount server-calculated → no client manipulation possible
- CHECK constraints at DB level → invalid values can never be stored

### What's Documented for Production (README)

| Feature | Production Approach |
|---|---|
| Database | SQLite → PostgreSQL (connection string change only, code identical) |
| Caching | Redis cache for GET /products (TTL 5 min), invalidate on product update |
| Rate limiting | Max 5 req/min on /auth/login to prevent brute force |
| Background jobs | Replace FastAPI BackgroundTasks with Celery + Redis for retry logic and persistence |
| Duplicate payments | Idempotency key on POST /orders/{id}/pay — same request twice = same result |
| Monitoring | Sentry for error tracking + Prometheus for metrics |
| CI/CD | GitHub Actions pipeline |
| Stock reduction | Move to payment time (not order creation) with SELECT FOR UPDATE |
| Abandoned orders | Celery beat (cron) to scan and clean up stale CREATED orders daily |

---

## 9. Project Structure

```
order-management/
├── app/
│   ├── main.py              # FastAPI app entry point, route registration
│   ├── database.py          # DB connection + SQLAlchemy session
│   ├── models/              # SQLAlchemy DB models (what's stored)
│   │   ├── user.py
│   │   ├── product.py
│   │   ├── order.py
│   │   └── order_item.py
│   ├── schemas/             # Pydantic request/response shapes (what API accepts/returns)
│   │   ├── user.py
│   │   ├── product.py
│   │   └── order.py
│   ├── routers/             # Route handlers — HTTP in/out only
│   │   ├── auth.py
│   │   ├── products.py
│   │   └── orders.py
│   ├── services/            # Business logic — all real work happens here
│   │   ├── auth_service.py
│   │   ├── product_service.py
│   │   └── order_service.py
│   ├── middleware/
│   │   └── auth.py          # JWT verification dependency
│   └── workers/
│       └── invoice.py       # Background job for invoice generation
├── migrations/              # Alembic migration files
│   └── versions/
├── tests/
│   ├── test_auth.py
│   ├── test_products.py
│   └── test_orders.py
├── .env                     # Environment variables (never commit)
├── .env.example             # Template showing what vars are needed
├── requirements.txt
├── README.md
├── alembic.ini
└── Dockerfile
```

---

## 10. Background Tasks Flow

```
POST /v1/orders/{id}/pay
        │
        ▼
Payment succeeds, order → PAID
        │
        ▼
API returns 200 immediately (does NOT wait for tasks)
        │
        ▼ (non-blocking, runs in background)
BackgroundTasks.add_task()
  ├── generate_invoice(order_id)
  │     → Creates PDF invoice
  │     → Saves to filesystem or object storage
  │
  └── send_email_notification(user_email, order_id)
        → Sends order confirmation email

Production upgrade:
  → Replace with RabbitMQ queue
  → Workers consume from queue
  → Failed jobs retry automatically
  → Dead letter queue for permanently failed jobs
```

---

## 11. Testing Plan

```
tests/test_auth.py
  → Register with valid data → 201
  → Register duplicate email → 409
  → Login valid credentials → 200 + JWT
  → Login wrong password → 401 "Invalid credentials"
  → Access protected route without token → 401
  → Access admin route as customer → 403

tests/test_products.py
  → Admin create product → 201
  → Customer create product → 403
  → List products with pagination → 200
  → Search products → 200 filtered results
  → Update product as admin → 200
  → Delete product as admin → 200 (soft delete)

tests/test_orders.py
  → Create order with valid items → 201
  → Create order insufficient stock → 400
  → Create order invalid product → 404
  → Pay order → 200, status → PAID
  → Pay already paid order → 400
  → Cancel order → 200, status → CANCELLED
  → Pay cancelled order → 400
  → View own orders as customer → 200
  → View other customer orders → 403
  → View all orders as admin → 200
```

---

## 12. Environment Variables

```env
# Database
DATABASE_URL=sqlite:///./order_management.db

# JWT
JWT_SECRET_KEY=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=60

# App
APP_ENV=development
API_VERSION=v1
```

---

## 13. Key Decisions Summary (For Mohammed Walkthrough)

| Decision | What | Why |
|---|---|---|
| FastAPI | Framework choice | Right balance — not too minimal (Flask), not too heavy (Django) |
| SQLite | Dev DB | Zero config, acceptable per assignment, SQLAlchemy makes it swappable |
| unit_price snapshot | Store price at order time | Product prices change — historical orders must show correct totals |
| Soft delete on products | is_available = false | Old orders reference products — hard delete breaks data integrity |
| Server-side total | Never trust client total | Client can manipulate sent total — always recalculate |
| ACID transaction on payment | SELECT FOR UPDATE | Prevents race condition / overselling with concurrent requests |
| JWT stateless auth | No server sessions | Enables horizontal scaling — any server can handle any request |
| Services layer | Business logic separate from routers | Maintainability — adding features doesn't touch existing route code |
| API versioning /v1/ | From day one | Future breaking changes go to /v2/ without affecting current clients |
| Keep cancelled orders | Don't delete | Admin can use for marketing reminders and analytics |
| 15-day TTL on abandoned orders | Auto-cleanup | Keeps DB clean without losing potential re-engagement window |
| Consistent error shape | Same JSON structure always | Frontend/clients handle errors predictably |
| 401 vs 403 | Correct HTTP semantics | 401 = not authenticated, 403 = authenticated but not authorized |
