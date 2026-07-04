"""Shared pytest fixtures.

Each test gets an isolated in-memory SQLite database (StaticPool keeps a single
connection so all sessions in the test share the same in-memory DB). The FastAPI
`get_db` dependency is overridden to use this test session.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection -> shared in-memory DB
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        try:
            yield db_session  # every request in the test reuses this session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---- helpers / shared users ----
def register(client, email, password="secret123", role="customer"):
    return client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "role": role},
    )


def login_token(client, email, password="secret123"):
    r = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_token(client):
    register(client, "admin@example.com", role="admin")
    return login_token(client, "admin@example.com")


@pytest.fixture()
def customer_token(client):
    register(client, "cust@example.com", role="customer")
    return login_token(client, "cust@example.com")


def make_product(client, admin_token, name="Laptop", price=1200.00, stock=10, available=True):
    r = client.post(
        "/v1/products",
        headers=auth_header(admin_token),
        json={"name": name, "price": price, "stock": stock, "is_available": available},
    )
    assert r.status_code == 201, r.text
    return r.json()
