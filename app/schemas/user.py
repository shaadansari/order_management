from pydantic import BaseModel, ConfigDict, EmailStr, Field

VALID_ROLES = ("customer", "admin")


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(default="customer")

    # NOTE: allowing the caller to self-assign role='admin' at registration is a
    # convenience for this assignment so admin flows are easy to demo/test. In
    # production, admin accounts must be provisioned via an invite/admin panel only —
    # open self-registration of admins would be a privilege-escalation hole.


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    """Public user representation — never includes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
