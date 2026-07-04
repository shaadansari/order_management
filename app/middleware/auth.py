"""Auth dependencies injected via FastAPI's `Depends`.

The folder is named 'middleware' to match the design doc; in FastAPI these are
dependencies rather than ASGI middleware, but they play the same gatekeeping role.
"""
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from ..config import settings
from ..core.errors import ForbiddenError, UnauthorizedError
from ..core.security import decode_access_token
from ..database import get_db
from ..models import User

# tokenUrl only powers Swagger's "Authorize" button. We accept a raw JWT in the
# Authorization header ("Bearer <jwt>"); we don't implement the OAuth2 password flow.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"/{settings.api_version}/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the JWT to a User, or raise 401.

    WHY 401 (not 403) here: a missing/expired/bad token means "I don't know who you are".
    """
    try:
        payload = decode_access_token(token)
    except jwt.InvalidTokenError:
        raise UnauthorizedError("Invalid or expired token")

    user_id = payload.get("sub")
    if user_id is None:
        raise UnauthorizedError("Invalid token payload")

    user = db.get(User, int(user_id))
    if user is None:
        # Valid token for a deleted user — treat as unauthenticated.
        raise UnauthorizedError("User not found")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Allow only admins. WHY 403: we know who you are, you're just not allowed."""
    if user.role != "admin":
        raise ForbiddenError("Admin access required")
    return user


def require_customer(user: User = Depends(get_current_user)) -> User:
    """Allow only customers (order endpoints are customer-facing per the design)."""
    if user.role != "customer":
        raise ForbiddenError("Customer access required")
    return user
