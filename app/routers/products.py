from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.auth import require_admin
from ..models import User
from ..schemas.product import ProductCreate, ProductListOut, ProductOut, ProductUpdate
from ..services import product_service

# Public + admin product routes share the /products prefix.
router = APIRouter(prefix="/products", tags=["products"])
# Admin "my products" lives under /admin/products per the design.
admin_products_router = APIRouter(prefix="/admin/products", tags=["products"])


# ---- Public ----
@router.get("", response_model=ProductListOut)
def list_products(
    search: str | None = Query(default=None, description="Substring match on name/description"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = product_service.list_public_products(db, search, limit, offset)
    return ProductListOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return product_service.get_public_product(db, product_id)


# ---- Admin ----
@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return product_service.create_product(db, admin.id, payload)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return product_service.update_product(db, product_id, admin.id, payload)


@router.delete("/{product_id}", response_model=ProductOut)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # Soft delete: sets is_available=false (see product_service.soft_delete_product).
    return product_service.soft_delete_product(db, product_id, admin.id)


# ---- Admin: own products only ----
@admin_products_router.get("", response_model=ProductListOut)
def list_my_products(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    items = product_service.list_admin_products(db, admin.id)
    count = len(items)
    return ProductListOut(items=items, total=count, limit=count, offset=0)
