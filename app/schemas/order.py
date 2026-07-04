from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)  # a line must order at least one unit


class OrderCreate(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1)  # an order cannot be empty


class OrderItemOut(BaseModel):
    product_name: str
    quantity: int
    unit_price: float       # SNAPSHOT price the customer paid at order time
    current_stock: int      # LIVE product stock right now (not the snapshot)


class CustomerOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: int
    status: str
    total_amount: float
    created_at: datetime
    items: list[OrderItemOut]


class CustomerOrderListOut(BaseModel):
    items: list[CustomerOrderOut]
    total: int


class AdminCustomerOut(BaseModel):
    id: int
    email: str


class AdminOrderOut(BaseModel):
    order_id: int
    status: str
    total_amount: float
    created_at: datetime
    customer: AdminCustomerOut
    items: list[OrderItemOut]


class AdminOrderListOut(BaseModel):
    items: list[AdminOrderOut]
    total: int


class OrderActionOut(BaseModel):
    """Minimal confirmation for pay/cancel actions."""

    order_id: int
    status: str
    total_amount: float


class PaginatedMeta(BaseModel):
    total: int
    limit: int
    offset: int
