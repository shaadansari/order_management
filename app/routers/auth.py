from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from ..core.limiter import limiter
from ..database import get_db
from ..schemas.user import TokenOut, UserOut, UserRegister
from ..services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
def register(request: Request, response: Response, payload: UserRegister, db: Session = Depends(get_db)):
    # WHY 3/min: registration is rarely called by a real user but trivially scriptable by a
    # spammer creating throwaway accounts, so it gets the tightest limit.
    # `response` is injected by FastAPI so slowapi can attach X-RateLimit-* headers to it.
    return auth_service.register(db, payload)


@router.post("/login", response_model=TokenOut)
@limiter.limit("5/minute")
def login(
    request: Request,
    response: Response,  # injected by FastAPI so slowapi can attach X-RateLimit-* headers
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # WHY 5/min: login is the primary brute-force target (an attacker scripts thousands of
    # password guesses). 5 attempts/minute/IP lets a real user retry after a typo while
    # making online password guessing impractical. form.username is the email (OAuth2 uses
    # 'username' as the field name).
    user, token = auth_service.authenticate(db, form.username, form.password)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))
