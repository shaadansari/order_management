from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.user import TokenOut, UserLogin, UserOut, UserRegister
from ..services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    return auth_service.register(db, payload)


@router.post("/login", response_model=TokenOut)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    # form.username is the email — OAuth2 spec uses 'username' as the field name
    user, token = auth_service.authenticate(db, form.username, form.password)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))