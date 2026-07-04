from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.auth import require_admin, require_customer
from ..models import OrderStatus, User
from ..schemas.order import (
    AdminOrderListOut,
    CustomerOrderListOut,
    OrderActionOut,
    OrderCreate,
)
from ..services import order_service
from ..workers.invoice import generate_invoice, send_email_notification

customer_router = APIRouter(prefix="/orders", tags=["orders"])
admin_orders_router = APIRouter(prefix="/admin/orders", tags=["orders"])


@customer_router.post("", status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    customer: User = Depends(require_customer),
):
    order = order_service.create_order(db, customer.id, payload)
    return order_service.to_customer_order_out(order)


@customer_router.post("/{order_id}/pay", response_model=OrderActionOut)
def pay_order(
    order_id: int,
    background_tasks: BackgroundTasks,
    force_fail: bool = Query(
        default=False,
        description="Testing hook: simulate a declined payment (-> 402)",
    ),
    db: Session = Depends(get_db),
    customer: User = Depends(require_customer),
):
    order = order_service.pay_order(db, customer.id, order_id, force_fail=force_fail)

    # WHY background tasks + only primitives passed: invoice/email are NOT part of the
    # critical payment path, so we run them after the response returns (non-blocking).
    # We pass order_id (not the ORM object) because the request's DB session closes once
    # the response is sent; the worker re-opens its own session. See workers/invoice.py.
    if order.status == OrderStatus.PAID.value:
        background_tasks.add_task(generate_invoice, order.id)
        background_tasks.add_task(send_email_notification, customer.email, order.id)

    return OrderActionOut(
        order_id=order.id, status=order.status, total_amount=float(order.total_amount)
    )


@customer_router.post("/{order_id}/cancel", response_model=OrderActionOut)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    customer: User = Depends(require_customer),
):
    order = order_service.cancel_order(db, customer.id, order_id)
    return OrderActionOut(
        order_id=order.id, status=order.status, total_amount=float(order.total_amount)
    )


@customer_router.get("", response_model=CustomerOrderListOut)
def list_my_orders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    customer: User = Depends(require_customer),
):
    orders, total = order_service.list_customer_orders(db, customer.id, limit, offset)
    return CustomerOrderListOut(
        items=[order_service.to_customer_order_out(o) for o in orders], total=total
    )


# ---- Admin: all orders ----
@admin_orders_router.get("", response_model=AdminOrderListOut)
def list_all_orders(
    status_: str | None = Query(default=None, alias="status", description="Filter by CREATED|PAID|CANCELLED"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    orders, total = order_service.list_admin_orders(db, limit, offset, status_)
    return AdminOrderListOut(
        items=[order_service.to_admin_order_out(o) for o in orders], total=total
    )
