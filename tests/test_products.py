"""Product flow tests — see design §11."""
from .conftest import auth_header, make_product


def test_admin_creates_product_201(client, admin_token):
    r = client.post(
        "/v1/products",
        headers=auth_header(admin_token),
        json={"name": "Laptop", "description": "Fast", "price": 1200.00, "stock": 5},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Laptop"
    assert body["price"] == 1200.0
    assert body["stock"] == 5
    assert body["is_available"] is True


def test_customer_cannot_create_product_403(client, customer_token):
    r = client.post(
        "/v1/products",
        headers=auth_header(customer_token),
        json={"name": "Laptop", "price": 10, "stock": 1},
    )
    assert r.status_code == 403


def test_list_products_pagination(client, admin_token):
    for i in range(5):
        make_product(client, admin_token, name=f"P{i}")
    r = client.get("/v1/products", params={"limit": 2, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_search_products(client, admin_token):
    make_product(client, admin_token, name="Gaming Laptop")
    make_product(client, admin_token, name="Office Mouse")
    r = client.get("/v1/products", params={"search": "laptop"})
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["items"]]
    assert names == ["Gaming Laptop"]


def test_product_list_cache_invalidated_on_create(client, admin_token, fake_cache):
    # Prime the cache with a public list call (default search=None, limit=20, offset=0).
    client.get("/v1/products")
    assert "products:list:None:20:0" in fake_cache.store

    # Creating a product must invalidate the whole products:list:* family.
    make_product(client, admin_token, name="Cached?")
    assert "products:list:None:20:0" not in fake_cache.store


def test_update_product_as_owner_admin(client, admin_token):
    product = make_product(client, admin_token, name="Laptop", price=100, stock=3)
    r = client.put(
        f"/v1/products/{product['id']}",
        headers=auth_header(admin_token),
        json={"price": 89.99, "stock": 20},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["price"] == 89.99
    assert body["stock"] == 20
    assert body["name"] == "Laptop"  # unchanged field preserved


def test_admin_cannot_update_other_admins_product(client):
    # First admin
    from .conftest import register, login_token
    register(client, "a1@example.com", role="admin")
    t1 = login_token(client, "a1@example.com")
    product = make_product(client, t1, name="Owned by a1")
    # Second admin tries to edit it
    register(client, "a2@example.com", role="admin")
    t2 = login_token(client, "a2@example.com")
    r = client.put(
        f"/v1/products/{product['id']}",
        headers=auth_header(t2),
        json={"price": 1},
    )
    assert r.status_code == 403


def test_soft_delete_hides_from_public_and_preserves_record(client, admin_token):
    product = make_product(client, admin_token, name="Laptop")
    r = client.delete(f"/v1/products/{product['id']}", headers=auth_header(admin_token))
    assert r.status_code == 200
    assert r.json()["is_available"] is False
    # Hidden from public list/detail
    assert client.get("/v1/products").json()["total"] == 0
    assert client.get(f"/v1/products/{product['id']}").status_code == 404
    # But admin can still see it in their own products
    mine = client.get("/v1/admin/products", headers=auth_header(admin_token)).json()
    assert mine["total"] == 1
    assert mine["items"][0]["is_available"] is False


def test_admin_can_list_only_their_own_products(client):
    from .conftest import register, login_token
    register(client, "a1@example.com", role="admin")
    t1 = login_token(client, "a1@example.com")
    register(client, "a2@example.com", role="admin")
    t2 = login_token(client, "a2@example.com")
    make_product(client, t1, name="A1 product")
    make_product(client, t1, name="another A1")
    make_product(client, t2, name="A2 product")

    mine1 = client.get("/v1/admin/products", headers=auth_header(t1)).json()
    mine2 = client.get("/v1/admin/products", headers=auth_header(t2)).json()
    assert mine1["total"] == 2
    assert mine2["total"] == 1
