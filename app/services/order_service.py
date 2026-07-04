"""Order business logic: create, pay, cancel, listing, and response serializers.

The single most important correctness decision in the whole system lives in
`_reserve_stock_atomic`: an atomic conditional UPDATE to reserve stock, which
prevents overselling under concurrency on BOTH SQLite and PostgreSQL. `create_order`
is now a short orchestrator; the steps (merge, resolve, reserve, persist) live in
the private `_*` helpers so each concern reads as one named unit.
"""
from sqlalchemy import update
from sqlalchemy.orm import Session, joinedload, selectinload

from ..core.errors import APIError, ForbiddenError, NotFoundError
from ..models import Order, OrderItem, OrderStatus, Product
from ..schemas.order import (
    AdminCustomerOut,
    AdminOrderOut,
    CustomerOrderOut,
    OrderCreate,
    OrderItemOut,
)


# --------------------------------------------------------------------------- #
# Eager-loading for order responses
# --------------------------------------------------------------------------- #
# WHY eager-load: building an order response needs each item's product (its name
# and LIVE stock). Reading them lazily would be an N+1 — one query per item, per
# order. selectinload fetches all items + products for the order(s) in a fixed
# number of round trips regardless of line-item count.
_ORDER_ITEM_PRODUCT = selectinload(Order.items).selectinload(OrderItem.product)
_ADMIN_ORDER_OPTIONS = (_ORDER_ITEM_PRODUCT, joinedload(Order.user))


def _load_order_for_response(
    db: Session, order_id: int, *, include_user: bool = False
) -> Order | None:
    """Fetch one order with items + products (and optionally the customer) eager-loaded.

    The response serializers can then map it with NO further DB queries. WHY
    populate_existing(): create_order/cancel_order mutate stock via bulk UPDATEs that
    bypass the identity map (synchronize_session=False), so a Product already cached
    in the session would otherwise show STALE stock. populate_existing() forces every
    row loaded by this query to overwrite the cached instance, so current_stock
    reflects the committed value (e.g. 8 after ordering 2 of 10).
    """
    options = _ADMIN_ORDER_OPTIONS if include_user else (_ORDER_ITEM_PRODUCT,)
    return (
        db.query(Order)
        .options(*options)
        .populate_existing()
        .filter(Order.id == order_id)
        .one_or_none()
    )


# --------------------------------------------------------------------------- #
# CREATE
# --------------------------------------------------------------------------- #
def _merge_requested(items) -> dict[int, int]:
    """Collapse duplicate product ids in the request into one quantity each."""
    requested: dict[int, int] = {}
    for item in items:
        requested[item.product_id] = requested.get(item.product_id, 0) + item.quantity
    return requested


def _resolve_products(db: Session, requested: dict[int, int]):
    """Batch-read the requested products, validate availability, compute the total.

    Returns (line_items, total). The total is computed SERVER-SIDE from live DB
    prices — a client-sent total is never trusted.
    """
    products = db.query(Product).filter(Product.id.in_(requested.keys())).all()
    by_id = {p.id: p for p in products}

    line_items: list[tuple[Product, int]] = []
    for product_id, qty in requested.items():
        product = by_id.get(product_id)
        if product is None:
            raise NotFoundError(f"Product {product_id} not found")
        if not product.is_available:
            raise APIError(400, "PRODUCT_UNAVAILABLE", f"Product '{product.name}' is no longer available")
        line_items.append((product, qty))

    total = sum(product.price * qty for product, qty in line_items)
    return line_items, total


def _reserve_stock_atomic(db: Session, line_items: list[tuple[Product, int]]) -> None:
    """Atomically reserve (decrement) stock for every line item.

    WHY a conditional UPDATE per product instead of SELECT ... FOR UPDATE:
      * SQLite ignores FOR UPDATE (it has no row locks), so relying on it gives false
        safety during local dev.
      * `UPDATE ... SET stock = stock - :qty WHERE id = :id AND stock >= :qty` is ONE
        atomic statement. On SQLite writes are serialized (no two can interleave), and
        on Postgres the statement is atomic and takes a row lock. `rowcount` tells us
        whether it actually decremented. Race-free on both engines.
    If ANY item can't be reserved, we roll back the WHOLE order — undoing earlier
    successful decrements in this same transaction so no stock is lost to a half-built
    order. This is THE guard against overselling when two customers race for the last
    item.
    """
    for product, qty in line_items:
        result = db.execute(
            update(Product)
            .where(Product.id == product.id)
            .where(Product.stock >= qty)
            .values(stock=Product.stock - qty)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            db.rollback()
            raise APIError(
                400,
                "INSUFFICIENT_STOCK",
                f"Insufficient stock for product '{product.name}'",
            )


def _persist_order(db: Session, user_id: int, line_items: list[tuple[Product, int]], total) -> Order:
    """Create the Order + OrderItem rows and commit the whole transaction atomically.

    WHY flush() before adding items: it assigns order.id (needed for the
    OrderItem.order_id FK) WITHOUT ending the transaction, so the stock decrements
    from _reserve_stock_atomic and these rows all commit together. unit_price is a
    SNAPSHOT of the product price at order time (see OrderItem docstring).
    """
    order = Order(user_id=user_id, status=OrderStatus.CREATED.value, total_amount=total)
    db.add(order)
    db.flush()  # assigns order.id without committing yet

    for product, qty in line_items:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=qty,
                unit_price=product.price,  # SNAPSHOT — see OrderItem docstring
            )
        )

    db.commit()
    return order


def create_order(db: Session, user_id: int, data: OrderCreate) -> Order:
    """Reserve stock and create a CREATED order. Atomic — never oversells.

    Per the assignment, stock is reduced at order creation (not at payment).
    """
    requested = _merge_requested(data.items)
    line_items, total = _resolve_products(db, requested)
    _reserve_stock_atomic(db, line_items)
    order = _persist_order(db, user_id, line_items, total)

    # The order's stock was just mutated by a bulk UPDATE that bypasses the identity
    # map, so reload the order (with items + products eager-loaded) for a response
    # that reflects the committed stock. See _load_order_for_response.
    return _load_order_for_response(db, order.id)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_owned_order(db: Session, user_id: int, order_id: int) -> Order:
    """Fetch an order and verify it belongs to the calling customer."""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise NotFoundError("Order not found")
    if order.user_id != user_id:
        # The design specifies 403 for "not your order". (A 404 would leak less, but we
        # follow the design.)
        raise ForbiddenError("This order does not belong to you")
    return order


def _simulate_payment_gateway(order: Order) -> bool:
    """Placeholder for a real payment provider (Stripe, etc.). Always succeeds here."""
    return True


# --------------------------------------------------------------------------- #
# PAY
# --------------------------------------------------------------------------- #
def pay_order(db: Session, user_id: int, order_id: int, force_fail: bool = False) -> Order:
    """Mark a CREATED order as PAID.

    `force_fail` is a testing/demo hook so the 402 payment-failure path can be
    exercised end-to-end (see POST /v1/orders/{id}/pay?force_fail=true).
    """
    order = _load_owned_order(db, user_id, order_id)

    if order.status == OrderStatus.PAID.value:
        raise APIError(400, "ORDER_ALREADY_PAID", "Order is already paid")
    if order.status == OrderStatus.CANCELLED.value:
        raise APIError(400, "ORDER_NOT_PAYABLE", "Cannot pay a cancelled order")

    # ---- Defensive stock re-check at payment time ----
    # Stock was reserved at creation, but an admin could have lowered a product's stock
    # below the ordered quantity in the meantime. In that case we refuse to charge
    # rather than ship something we can't fulfil.
    for item in order.items:
        product = db.get(Product, item.product_id)
        if product is None or product.stock < 0:
            raise APIError(
                400, "OUT_OF_STOCK_AT_PAYMENT", "A product in your order is out of stock"
            )

    # ---- Simulate payment (ROLLBACK on failure = don't set PAID) ----
    if force_fail or not _simulate_payment_gateway(order):
        db.rollback()
        raise APIError(402, "PAYMENT_FAILED", "Payment was declined by the payment provider")

    order.status = OrderStatus.PAID.value
    db.commit()
    db.refresh(order)
    return order


# --------------------------------------------------------------------------- #
# CANCEL
# --------------------------------------------------------------------------- #
def cancel_order(db: Session, user_id: int, order_id: int) -> Order:
    """Cancel a CREATED order and RETURN its reserved stock.

    WHY restore stock here (the design doc omits this step): stock was reserved at
    creation. If cancel doesn't give it back, every cancelled order permanently "loses"
    that inventory — a real inventory leak. Restoring is the correct behaviour.
    """
    order = _load_owned_order(db, user_id, order_id)

    if order.status != OrderStatus.CREATED.value:
        raise APIError(
            400, "ORDER_NOT_CANCELLABLE", "Only pending (CREATED) orders can be cancelled"
        )

    for item in order.items:
        db.execute(
            update(Product)
            .where(Product.id == item.product_id)
            .values(stock=Product.stock + item.quantity)
            .execution_options(synchronize_session=False)
        )

    order.status = OrderStatus.CANCELLED.value
    db.commit()
    db.refresh(order)
    return order


# --------------------------------------------------------------------------- #
# LISTING
# --------------------------------------------------------------------------- #
def list_customer_orders(db: Session, user_id: int, limit: int, offset: int):
    # Eager-load items+products so to_customer_order_out needs no extra queries.
    q = (
        db.query(Order)
        .options(_ORDER_ITEM_PRODUCT)
        .filter(Order.user_id == user_id)
        .order_by(Order.id.desc())
    )
    total = q.count()
    return q.offset(offset).limit(limit).all(), total


def list_admin_orders(db: Session, limit: int, offset: int, status: str | None = None):
    # Eager-load items+products+customer so to_admin_order_out needs no extra queries.
    q = db.query(Order).options(*_ADMIN_ORDER_OPTIONS)
    if status:
        q = q.filter(Order.status == status.upper())
    total = q.count()
    return q.order_by(Order.id.desc()).offset(offset).limit(limit).all(), total


# --------------------------------------------------------------------------- #
# SERIALIZERS (ORM -> response schema)
# --------------------------------------------------------------------------- #
# These map ORM rows to the response shapes in the design's "Response Shapes" section.
# They are PURE mappers: callers must pass an order whose items + products have been
# eager-loaded (see _load_order_for_response / the list queries), so `item.product`
# and `current_stock` are available without any DB read here. `current_stock` is the
# LIVE product stock, not the order snapshot.
def _item_to_out(item: OrderItem) -> OrderItemOut:
    # product is eager-loaded by the caller; it can only be None if the product row was
    # hard-deleted out from under a historical order_item (we soft-delete, so normally set).
    product = item.product
    return OrderItemOut(
        product_name=product.name if product else "(deleted)",
        quantity=item.quantity,
        unit_price=float(item.unit_price),
        current_stock=product.stock if product else 0,
    )


def to_customer_order_out(order: Order) -> CustomerOrderOut:
    return CustomerOrderOut(
        order_id=order.id,
        status=order.status,
        total_amount=float(order.total_amount),
        created_at=order.created_at,
        items=[_item_to_out(i) for i in order.items],
    )


def to_admin_order_out(order: Order) -> AdminOrderOut:
    return AdminOrderOut(
        order_id=order.id,
        status=order.status,
        total_amount=float(order.total_amount),
        created_at=order.created_at,
        customer=AdminCustomerOut(id=order.user.id, email=order.user.email),
        items=[_item_to_out(i) for i in order.items],
    )
