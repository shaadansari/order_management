"""Auth flow tests — see design §11."""
from .conftest import auth_header, login_token, register


def test_register_valid_returns_201(client):
    r = register(client, "new@example.com")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "customer"
    assert "password" not in body  # password hash must never leak


def test_register_duplicate_email_returns_409(client):
    register(client, "dup@example.com")
    r = register(client, "dup@example.com")
    assert r.status_code == 409
    assert r.json()["error"] == "DUPLICATE_EMAIL"


def test_register_rejects_invalid_email(client):
    r = client.post("/v1/auth/register", json={"email": "not-an-email", "password": "secret123"})
    assert r.status_code == 422


def test_login_valid_returns_token(client):
    register(client, "ok@example.com")
    r = client.post("/v1/auth/login", data={"username": "ok@example.com", "password": "secret123"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "ok@example.com"


def test_login_wrong_password_returns_401_generic_message(client):
    register(client, "ok2@example.com")
    r = client.post(
        "/v1/auth/login", data={"username": "ok2@example.com", "password": "WRONG"}
    )
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "INVALID_CREDENTIALS"
    # Generic message — must not reveal whether the email exists.
    assert body["message"] == "Invalid credentials"


def test_login_unknown_user_returns_same_401(client):
    # Same shape/message as wrong-password — no account enumeration.
    r = client.post("/v1/auth/login", data={"username": "ghost@example.com", "password": "whatever"})
    assert r.status_code == 401
    assert r.json()["message"] == "Invalid credentials"


def test_protected_route_without_token_returns_401(client):
    r = client.post("/v1/products", json={"name": "X", "price": 1, "stock": 1})
    assert r.status_code == 401
    assert r.json()["error"] == "UNAUTHORIZED"


def test_admin_route_as_customer_returns_403(client, customer_token):
    r = client.post(
        "/v1/products",
        headers=auth_header(customer_token),
        json={"name": "X", "price": 1, "stock": 1},
    )
    assert r.status_code == 403
    assert r.json()["error"] == "FORBIDDEN"


def test_consistent_error_shape(client):
    # Every error shares the {error, message, status} shape.
    r = client.post("/v1/products", json={"name": "X", "price": 1, "stock": 1})
    body = r.json()
    assert set(["error", "message", "status"]).issubset(body.keys())
    assert body["status"] == r.status_code
