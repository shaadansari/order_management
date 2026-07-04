"""Shared pytest fixtures.

Each test gets an isolated in-memory SQLite database (StaticPool keeps a single
connection so all sessions in the test share the same in-memory DB). The FastAPI
`get_db` dependency is overridden to use this test session.
"""
import fnmatch
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.limiter import limiter
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


class FakeRedisCache:
    """In-memory stand-in for RedisCache so tests never need a real Redis.

    Mirrors app.core.cache.RedisCache (get/set/delete/delete_pattern). delete_pattern uses
    glob matching (fnmatch), matching Redis SCAN patterns. ttl is accepted but ignored.
    """

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)

    def delete_pattern(self, pattern):
        for k in list(self.store):
            if fnmatch.fnmatch(k, pattern):
                del self.store[k]


@pytest.fixture(autouse=True)
def fake_cache(monkeypatch):
    """Swap the Redis cache singleton for an in-memory fake for every test.

    WHY autouse: guarantees no test talks to a real Redis (hermetic) and gives each test a
    fresh cache. Tests that need to inspect cache state request `fake_cache` by name.
    """
    fake = FakeRedisCache()
    monkeypatch.setattr("app.services.product_service.cache", fake)
    return fake


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # slowapi keeps counters in memory; clear them between tests so one test's auth bursts
    # (and the dedicated rate-limit tests) don't push the next test over /auth/login (5/min)
    # or /auth/register (3/min).
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def celery_tasks(monkeypatch):
    """Mock the 3 Celery tasks where orders.py imports them so tests never need a broker.

    Returns a dict of the mocks keyed by task name, so individual tests can assert which tasks
    were fired and with what args. Fresh Mocks per test (function scope) -> call counts reset.
    """
    tasks = {
        "generate_invoice": Mock(),
        "send_order_notification": Mock(),
        "check_low_stock": Mock(),
    }
    for name, mock in tasks.items():
        monkeypatch.setattr(f"app.routers.orders.{name}", mock)
    return tasks


# ---- helpers / shared users ----
def register(client, email, password="secret123", role="customer"):
    return client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "role": role},
    )


def login_token(client, email, password="secret123"):
    # The /v1/auth/login route uses OAuth2PasswordRequestForm (form fields username/password,
    # where username is the email) so the /docs "Authorize" button works — send form data.
    r = client.post("/v1/auth/login", data={"username": email, "password": password})
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
