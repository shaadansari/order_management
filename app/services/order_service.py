"""Order business logic: create, pay, cancel, listing, and response serializers.

Stock model (production-correct): stock is NOT touched at order creation — an order is
just a CREATED intent with a price snapshot. Stock is decremented atomically only on
successful payment (`_reduce_stock_atomic` inside `pay_order`), which is where the
oversell race actually lives. Cancelling a CREATED order therefore touches no stock
(nothing was ever reserved).

`create_order` / `pay_order` / `cancel_order` are short orchestrators; the steps live
in the private `_*` helpers so each concern reads as one named unit.
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
    """Batch-read the requested products, validate they exist and are available.

    Returns (line_items, total). The total is computed SERVER-SIDE from live DB
    prices — a client-sent total is never trusted.

    NOTE: stock is intentionally NOT validated here. Stock is only checked (and
    decremented) at payment time; an order is a price-snapped intent, not a reservation.
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


def _persist_order(db: Session, user_id: int, line_items: list[tuple[Product, int]], total) -> Order:
    """Create the Order + OrderItem rows and commit.

    WHY flush() before adding items: it assigns order.id (needed for the
    OrderItem.order_id FK) WITHOUT ending the transaction. unit_price is a SNAPSHOT of
    the product price at order time (see OrderItem docstring). Stock is NOT modified.
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
    """Create a CREATED order (a price-snapped intent). Does NOT touch stock.

    Per the production model, stock is reduced only on successful payment — never at
    creation. See pay_order.
    """
    requested = _merge_requested(data.items)
    line_items, total = _resolve_products(db, requested)
    order = _persist_order(db, user_id, line_items, total)

    # Reload with items + products eager-loaded for the response. No populate_existing()
    # needed: create_order touches no stock, so nothing here is stale. current_stock is the
    # FULL stock (nothing reserved yet); it drops only after payment.
    return db.query(Order).options(_ORDER_ITEM_PRODUCT).filter(Order.id == order.id).first()


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


def _reduce_stock_atomic(db: Session, items) -> None:
    """Atomically decrement stock for every order item — THE oversell guard, at payment.

    WHY a conditional UPDATE per item instead of SELECT ... FOR UPDATE:
      * SQLite ignores FOR UPDATE (it has no row locks), so relying on it gives false
        safety during local dev.
      * `UPDATE ... SET stock = stock - :qty WHERE id = :id AND stock >= :qty` is ONE
        atomic statement. On SQLite writes are serialized (no two interleave), and on
        Postgres the statement is atomic and takes a row lock. `rowcount` tells us
        whether it actually decremented. Race-free on both engines.
    If ANY item can't be reduced, we roll back the WHOLE payment — undoing earlier
    successful decrements in this same transaction so no stock is lost to a half-paid
    order. This is what prevents two customers from buying the last item simultaneously.
    """
    for item in items:
        result = db.execute(
            update(Product)
            .where(Product.id == item.product_id)
            .where(Product.stock >= item.quantity)
            .values(stock=Product.stock - item.quantity)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            db.rollback()
            product = db.get(Product, item.product_id)
            name = product.name if product else f"id={item.product_id}"
            raise APIError(
                400,
                "INSUFFICIENT_STOCK",
                f"Insufficient stock for product '{name}'",
            )


def _simulate_payment_gateway(order: Order) -> bool:
    """Placeholder for a real payment provider (Stripe, etc.). Always succeeds here.

    Swap this body for a real gateway call when wiring up payments and return its verdict.
    The `force_fail` hook in pay_order covers the 402 decline path meanwhile.
    """
    return True


# --------------------------------------------------------------------------- #
# PAY
# --------------------------------------------------------------------------- #
def pay_order(
    db: Session,
    user_id: int,
    order_id: int,
    force_fail: bool = False,
) -> Order:
    """Mark a CREATED order as PAID: reserve stock atomically, then charge.

    Order of operations (one transaction):
      1. status checks
      2. atomically reserve stock for every item (refuse with INSUFFICIENT_STOCK before
         charging anything — never charge for inventory we can't fulfil)
      3. charge via `_simulate_payment_gateway`; on decline (or `force_fail`), roll back
         -> stock is released and the order stays CREATED (402 PAYMENT_FAILED)
      4. set PAID and commit

    `force_fail` is a testing/demo hook (POST /v1/orders/{id}/pay?force_fail=true) that
    forces the 402 path. `_simulate_payment_gateway` is the stub to swap for a real provider.
    """
    order = _load_owned_order(db, user_id, order_id)

    if order.status == OrderStatus.PAID.value:
        raise APIError(400, "ORDER_ALREADY_PAID", "Order is already paid")
    if order.status == OrderStatus.CANCELLED.value:
        raise APIError(400, "ORDER_NOT_PAYABLE", "Cannot pay a cancelled order")

    # 1) Reserve stock atomically. Raises INSUFFICIENT_STOCK (and rolls back) on the
    #    first item that can't be fulfilled, so we never charge for an unfulfillable order.
    _reduce_stock_atomic(db, order.items)

    # 2) Charge. On decline (or force_fail), roll back the reservation above so stock is
    #    NOT reduced and the order remains CREATED.
    if force_fail or not _simulate_payment_gateway(order):
        db.rollback()
        raise APIError(402, "PAYMENT_FAILED", "Payment was declined by the payment provider")

    # 3) Finalize, then reload with items + products eager-loaded (db.refresh would not
    #    load the relations).
    order.status = OrderStatus.PAID.value
    db.commit()
    return db.query(Order).options(_ORDER_ITEM_PRODUCT).filter(Order.id == order.id).first()


# --------------------------------------------------------------------------- #
# CANCEL
# --------------------------------------------------------------------------- #
def cancel_order(db: Session, user_id: int, order_id: int) -> Order:
    """Cancel a CREATED order.

    Stock is NOT restored here: because stock is only ever reduced on successful
    payment, a CREATED order holds no inventory — there is nothing to give back.
    Cancelling simply flips the status to CANCELLED. (Only CREATED orders are
    cancellable; a PAID order has already reduced stock and would need a refund flow,
    not a cancel.) The order and its items are kept for admin/marketing history.
    """
    order = _load_owned_order(db, user_id, order_id)

    if order.status != OrderStatus.CREATED.value:
        raise APIError(
            400, "ORDER_NOT_CANCELLABLE", "Only pending (CREATED) orders can be cancelled"
        )

    order.status = OrderStatus.CANCELLED.value
    db.commit()
    return db.query(Order).options(_ORDER_ITEM_PRODUCT).filter(Order.id == order.id).first()


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
# eager-loaded (see create_order/pay_order/cancel_order + the list queries), so
# `item.product` and `current_stock` are available without any DB read here.
# `current_stock` is the LIVE product stock (full at creation, reduced after payment),
# not the order snapshot.
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
