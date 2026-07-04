"""Order business logic: create, pay, cancel, listing, and response serializers.

The single most important correctness decision in the whole system lives in
`create_order`: an atomic conditional UPDATE to reserve stock, which prevents
overselling under concurrency on BOTH SQLite and PostgreSQL.
"""
from sqlalchemy import update
from sqlalchemy.orm import Session

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
# CREATE
# --------------------------------------------------------------------------- #
def create_order(db: Session, user_id: int, data: OrderCreate) -> Order:
    """Reserve stock and create a CREATED order. Atomic — never oversells.

    Per the assignment, stock is reduced at order creation (not at payment).
    """
    # ---- 1) Validate inputs + resolve products (read phase) ----
    # Merge duplicate product ids into one requested quantity each.
    requested: dict[int, int] = {}
    for item in data.items:
        requested[item.product_id] = requested.get(item.product_id, 0) + item.quantity

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

    # Total is computed SERVER-SIDE from current DB prices — never trust a client total.
    total = sum(product.price * qty for product, qty in line_items)

    # ---- 2) Atomically reserve stock (write phase) ----
    # WHY a conditional UPDATE per product instead of SELECT ... FOR UPDATE:
    #   * SQLite ignores FOR UPDATE (it has no row locks), so relying on it gives false
    #     safety during local dev.
    #   * `UPDATE ... SET stock = stock - :qty WHERE id = :id AND stock >= :qty` is ONE
    #     atomic statement. On SQLite writes are serialized (no two can interleave), and
    #     on Postgres the statement is atomic and takes a row lock. `rowcount` tells us
    #     whether it actually decremented. Race-free on both engines.
    # This is THE guard against overselling when two customers race for the last item.
    for product, qty in line_items:
        result = db.execute(
            update(Product)
            .where(Product.id == product.id)
            .where(Product.stock >= qty)
            .values(stock=Product.stock - qty)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            # Stock dropped below qty (or row changed under us): refuse the WHOLE order.
            db.rollback()
            raise APIError(
                400,
                "INSUFFICIENT_STOCK",
                f"Insufficient stock for product '{product.name}'",
            )

    # ---- 3) Persist order + line items (snapshot unit_price) ----
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
    db.refresh(order)
    return order


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
    q = db.query(Order).filter(Order.user_id == user_id).order_by(Order.id.desc())
    total = q.count()
    return q.offset(offset).limit(limit).all(), total


def list_admin_orders(db: Session, limit: int, offset: int, status: str | None = None):
    q = db.query(Order)
    if status:
        q = q.filter(Order.status == status.upper())
    total = q.count()
    return q.order_by(Order.id.desc()).offset(offset).limit(limit).all(), total


# --------------------------------------------------------------------------- #
# SERIALIZERS (ORM -> response schema)
# --------------------------------------------------------------------------- #
# These map ORM rows to the response shapes in the design's "Response Shapes" section.
# `current_stock` is the LIVE product stock (a fresh lookup), not the order snapshot.
def _item_to_out(item: OrderItem, db: Session) -> OrderItemOut:
    # WHY populate_existing(): the stock-decrement/restore in create/cancel use bulk
    # UPDATEs that bypass the session's identity map, so a cached Product would show a
    # STALE stock. populate_existing() forces a fresh read so current_stock reflects the
    # real DB value (e.g. 8 after ordering 2 from 10, matching the design's example).
    product = (
        db.query(Product)
        .filter(Product.id == item.product_id)
        .populate_existing()
        .first()
    )
    return OrderItemOut(
        product_name=product.name if product else "(deleted)",
        quantity=item.quantity,
        unit_price=float(item.unit_price),
        current_stock=product.stock if product else 0,
    )


def to_customer_order_out(order: Order, db: Session) -> CustomerOrderOut:
    return CustomerOrderOut(
        order_id=order.id,
        status=order.status,
        total_amount=float(order.total_amount),
        created_at=order.created_at,
        items=[_item_to_out(i, db) for i in order.items],
    )


def to_admin_order_out(order: Order, db: Session) -> AdminOrderOut:
    return AdminOrderOut(
        order_id=order.id,
        status=order.status,
        total_amount=float(order.total_amount),
        created_at=order.created_at,
        customer=AdminCustomerOut(id=order.user.id, email=order.user.email),
        items=[_item_to_out(i, db) for i in order.items],
    )
