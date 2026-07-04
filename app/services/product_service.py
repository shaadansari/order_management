from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.errors import ForbiddenError, NotFoundError
from ..models import Product
from ..schemas.product import ProductCreate, ProductUpdate


def create_product(db: Session, admin_id: int, data: ProductCreate) -> Product:
    product = Product(
        created_by=admin_id,
        name=data.name,
        description=data.description,
        price=data.price,
        is_available=data.is_available,
        stock=data.stock,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def list_public_products(db: Session, search: str | None, limit: int, offset: int):
    """Products visible to customers: only available ones, optional name/desc search."""
    # WHY is_available=True filter here: customers must never see soft-deleted products.
    q = db.query(Product).filter(Product.is_available.is_(True))
    if search:
        pattern = f"%{search}%"
        q = q.filter(or_(Product.name.ilike(pattern), Product.description.ilike(pattern)))
    total = q.count()
    items = q.order_by(Product.id.desc()).offset(offset).limit(limit).all()
    return items, total


def get_public_product(db: Session, product_id: int) -> Product:
    product = db.query(Product).filter(
        Product.id == product_id, Product.is_available.is_(True)
    ).first()
    if not product:
        raise NotFoundError("Product not found")
    return product


def _get_owned_product(db: Session, product_id: int, admin_id: int) -> Product:
    """Fetch a product and verify the calling admin created it.

    WHY ownership: created_by ties each product to its admin, matching GET
    /admin/products ("their own products"). Letting one admin edit another's product
    would break that boundary.
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise NotFoundError("Product not found")
    if product.created_by != admin_id:
        raise ForbiddenError("You can only modify products you created")
    return product


def update_product(db: Session, product_id: int, admin_id: int, data: ProductUpdate) -> Product:
    product = _get_owned_product(db, product_id, admin_id)
    # Apply only the fields the client actually sent (partial update via PUT).
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


def soft_delete_product(db: Session, product_id: int, admin_id: int) -> Product:
    product = _get_owned_product(db, product_id, admin_id)
    # WHY soft delete: preserve order-history integrity (old order_items point here).
    product.is_available = False
    db.commit()
    db.refresh(product)
    return product


def list_admin_products(db: Session, admin_id: int):
    """An admin sees ALL of their own products, including soft-deleted ones, to manage them."""
    return db.query(Product).filter(Product.created_by == admin_id).order_by(Product.id.desc()).all()
