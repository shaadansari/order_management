from sqlalchemy import Column, DateTime, Integer, String, Text, CheckConstraint, func
from sqlalchemy.orm import relationship

from ..database import Base


class User(Base):
    """An application user — either a 'customer' or an 'admin'."""

    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    # WHY unique + index on email: email is the login key, so lookups must be fast and
    # uniqueness must be enforced at the DB (app-level checks can race).
    email = Column(String, nullable=False, unique=True, index=True)
    # WHY Text for password: it stores a bcrypt HASH, never plaintext, so the length is
    # not predictable and we never want to truncate it.
    password = Column(Text, nullable=False)
    # WHY default 'customer': the overwhelming majority of users are customers; admin is
    # the exception granted out-of-band.
    role = Column(String, nullable=False, default="customer")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        # WHY DB-level CHECKs: last line of defense. Pydantic validates first, but if app
        # code ever has a bug, the DB still refuses to store invalid values.
        CheckConstraint("email LIKE '%_@__%.__%'", name="ck_user_email_format"),
        CheckConstraint("role IN ('customer', 'admin')", name="ck_user_role_valid"),
    )

    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    products = relationship("Product", back_populates="creator")
