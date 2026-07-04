from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    # WHY Decimal input (not float): preserves precision from the very first hop; stored
    # into a Numeric column. ge=0 prevents negative prices at validation time.
    price: Decimal = Field(ge=0)
    is_available: bool = True
    stock: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    """Partial update — only sent fields are applied (exclude_unset in the service)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(default=None, ge=0)
    is_available: Optional[bool] = None
    stock: Optional[int] = Field(default=None, ge=0)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    # WHY float on the way OUT: Decimal -> float at the response edge for clean JSON
    # numbers ({"price": 1200.0}). All internal math stays Decimal/precise.
    price: float
    is_available: bool
    stock: int
    created_by: int


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int
