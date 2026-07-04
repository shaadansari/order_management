from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.errors import APIError
from ..core.security import create_access_token, hash_password, verify_password
from ..models import User
from ..schemas.user import VALID_ROLES, UserRegister


def register(db: Session, data: UserRegister) -> User:
    # WHY an explicit existence check (instead of only relying on the unique constraint):
    # it lets us return a clean 409 DUPLICATE_EMAIL instead of surfacing a raw
    # IntegrityError to the client. We still handle the IntegrityError as a tiebreaker
    # for the race where two concurrent registrations slip past the check.
    if data.role not in VALID_ROLES:
        raise APIError(400, "INVALID_ROLE", "role must be 'customer' or 'admin'")

    if db.query(User).filter(User.email == data.email).first():
        raise APIError(409, "DUPLICATE_EMAIL", "An account with this email already exists")

    user = User(email=data.email, password=hash_password(data.password), role=data.role)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise APIError(409, "DUPLICATE_EMAIL", "An account with this email already exists")

    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> tuple[User, str]:
    user = db.query(User).filter(User.email == email).first()
    # WHY a single generic "Invalid credentials" message regardless of whether the email
    # exists: distinguishing "no such user" from "wrong password" lets attackers
    # enumerate which emails are registered.
    if not user or not verify_password(password, user.password):
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid credentials")

    token = create_access_token(user.id, user.role)
    return user, token
