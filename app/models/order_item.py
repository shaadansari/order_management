from sqlalchemy import Column, ForeignKey, Integer, Numeric
from sqlalchemy.orm import relationship

from ..database import Base


class OrderItem(Base):
    """One line of an order.

    NOTE on unit_price: this is a SNAPSHOT of the product price at the moment the order
    was placed, NOT a live join to product.price. Product prices change over time, but a
    historical order must always show the price the customer actually paid. This is how
    every real e-commerce system keeps order history correct.
    """

    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("order.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("product.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=False)  # price snapshot, see class docstring

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")
