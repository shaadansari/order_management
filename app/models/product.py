from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from ..database import Base


class Product(Base):
    """A sellable item, created by an admin."""

    __tablename__ = "product"

    id = Column(Integer, primary_key=True, index=True)
    # WHY created_by: audit trail + lets an admin manage ONLY their own products
    # (see GET /v1/admin/products and the ownership checks in product_service).
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    # WHY name is NOT unique: "Premium Laptop" and "Basic Laptop" are legitimate names;
    # a unique constraint would be too strict. We index it for fast search instead.
    name = Column(Text, nullable=False, index=True)
    description = Column(Text, nullable=True)
    # WHY Numeric(10,2), not REAL (float): float drift (0.1 + 0.2 != 0.3) corrupts money.
    # Numeric stores exact decimals and maps to DECIMAL on Postgres — same code, no
    # precision bug. The design suggested REAL; we upgrade to Numeric for correctness.
    price = Column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    # WHY is_available (soft delete): old order_items reference products, so hard-deleting
    # a product would break historical orders. We hide it instead.
    is_available = Column(Boolean, nullable=False, default=True)
    stock = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    creator = relationship("User", back_populates="products")
    order_items = relationship("OrderItem", back_populates="product")
