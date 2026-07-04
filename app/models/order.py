import enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import relationship

from ..database import Base


class OrderStatus(str, enum.Enum):
    """Lifecycle of an order: CREATED -> (PAID | CANCELLED).

    WHY a str Enum: values serialize to JSON cleanly AND compare to the DB's text column.
    """

    CREATED = "CREATED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class Order(Base):
    """A customer's order."""

    __tablename__ = "order"  # reserved SQL word — SQLAlchemy quotes reserved names automatically

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default=OrderStatus.CREATED.value)
    # WHY total_amount Numeric: it is computed SERVER-SIDE (never trust a client-sent
    # total), and money must be exact.
    total_amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'PAID', 'CANCELLED')", name="ck_order_status_valid"
        ),
    )

    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
