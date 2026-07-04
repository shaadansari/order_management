"""Order flow tests — see design §11. Includes the oversell / race-condition guard."""
from .conftest import auth_header, make_product, register, login_token


def _order(client, token, items):
    return client.post("/v1/orders", headers=auth_header(token), json={"items": items})


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
def test_create_order_valid(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=1200.00, stock=10)
    r = _order(client, customer_token, [{"product_id": p["id"], "quantity": 2}])
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "CREATED"
    assert body["total_amount"] == 2400.0  # server-side computed, not client-sent
    item = body["items"][0]
    assert item["product_name"] == "Laptop"
    assert item["quantity"] == 2
    assert item["unit_price"] == 1200.0   # snapshot
    assert item["current_stock"] == 8     # 10 - 2, live stock after reservation


def test_create_order_calculates_total_server_side(client, admin_token, customer_token):
    # Client sends no total at all; server computes from DB prices.
    p1 = make_product(client, admin_token, name="A", price=10.00, stock=5)
    p2 = make_product(client, admin_token, name="B", price=25.50, stock=5)
    r = _order(client, customer_token, [
        {"product_id": p1["id"], "quantity": 2},
        {"product_id": p2["id"], "quantity": 1},
    ])
    assert r.status_code == 201
    # 2*10 + 1*25.50 == 45.5
    assert r.json()["total_amount"] == 45.5


def test_create_order_insufficient_stock_400(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", stock=3)
    r = _order(client, customer_token, [{"product_id": p["id"], "quantity": 5}])
    assert r.status_code == 400
    assert r.json()["error"] == "INSUFFICIENT_STOCK"


def test_create_order_invalid_product_404(client, customer_token):
    r = _order(client, customer_token, [{"product_id": 9999, "quantity": 1}])
    assert r.status_code == 404


def test_create_order_merges_duplicate_product_lines(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=10)
    # Same product on two lines -> merged into qty 3.
    r = _order(client, customer_token, [
        {"product_id": p["id"], "quantity": 1},
        {"product_id": p["id"], "quantity": 2},
    ])
    assert r.status_code == 201
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 3
    assert body["total_amount"] == 30.0


# --------------------------------------------------------------------------- #
# THE OVERSELL GUARD (the race-condition protection from design §5)
# --------------------------------------------------------------------------- #
def test_no_oversell_when_stock_runs_out(client, admin_token, customer_token):
    # Only ONE item in stock. First order takes it; second must be refused.
    p = make_product(client, admin_token, name="Rare", price=5.00, stock=1)

    first = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}])
    assert first.status_code == 201
    assert first.json()["items"][0]["current_stock"] == 0

    # Register a second customer so we clearly have a different buyer.
    register(client, "other@example.com", role="customer")
    other_token = login_token(client, "other@example.com")
    second = _order(client, other_token, [{"product_id": p["id"], "quantity": 1}])
    assert second.status_code == 400
    assert second.json()["error"] == "INSUFFICIENT_STOCK"


# --------------------------------------------------------------------------- #
# Pay
# --------------------------------------------------------------------------- #
def test_pay_order_marks_paid(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    r = client.post(f"/v1/orders/{order['order_id']}/pay", headers=auth_header(customer_token))
    assert r.status_code == 200
    assert r.json()["status"] == "PAID"


def test_pay_already_paid_order_400(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    client.post(f"/v1/orders/{order['order_id']}/pay", headers=auth_header(customer_token))
    r = client.post(f"/v1/orders/{order['order_id']}/pay", headers=auth_header(customer_token))
    assert r.status_code == 400
    assert r.json()["error"] == "ORDER_ALREADY_PAID"


def test_pay_cancelled_order_400(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    client.post(f"/v1/orders/{order['order_id']}/cancel", headers=auth_header(customer_token))
    r = client.post(f"/v1/orders/{order['order_id']}/pay", headers=auth_header(customer_token))
    assert r.status_code == 400
    assert r.json()["error"] == "ORDER_NOT_PAYABLE"


def test_payment_failure_returns_402_and_leaves_created(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    # force_fail simulates a declined payment (the 402 path).
    r = client.post(
        f"/v1/orders/{order['order_id']}/pay",
        headers=auth_header(customer_token),
        params={"force_fail": "true"},
    )
    assert r.status_code == 402
    assert r.json()["error"] == "PAYMENT_FAILED"
    # Order remains CREATED after a failed payment.
    mine = client.get("/v1/orders", headers=auth_header(customer_token)).json()
    assert mine["items"][0]["status"] == "CREATED"


# --------------------------------------------------------------------------- #
# Cancel + stock restoration
# --------------------------------------------------------------------------- #
def test_cancel_order_marks_cancelled(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 2}]).json()
    r = client.post(f"/v1/orders/{order['order_id']}/cancel", headers=auth_header(customer_token))
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


def test_cancel_restores_stock(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=2)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 2}]).json()
    # Stock reserved: 2 -> 0
    assert order["items"][0]["current_stock"] == 0
    # Cancel returns the stock: 0 -> 2
    client.post(f"/v1/orders/{order['order_id']}/cancel", headers=auth_header(customer_token))
    # Now we can order 2 again (proves stock was restored, not leaked).
    again = _order(client, customer_token, [{"product_id": p["id"], "quantity": 2}])
    assert again.status_code == 201


def test_can_only_cancel_created_orders(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    client.post(f"/v1/orders/{order['order_id']}/pay", headers=auth_header(customer_token))
    r = client.post(f"/v1/orders/{order['order_id']}/cancel", headers=auth_header(customer_token))
    assert r.status_code == 400
    assert r.json()["error"] == "ORDER_NOT_CANCELLABLE"


# --------------------------------------------------------------------------- #
# Ownership + authorization
# --------------------------------------------------------------------------- #
def test_customer_cannot_touch_other_customers_order(client, admin_token):
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=5)
    register(client, "one@example.com", role="customer")
    t1 = login_token(client, "one@example.com")
    register(client, "two@example.com", role="customer")
    t2 = login_token(client, "two@example.com")

    order = _order(client, t1, [{"product_id": p["id"], "quantity": 1}]).json()
    # Customer two must be forbidden from paying/cancelling customer one's order.
    assert client.post(
        f"/v1/orders/{order['order_id']}/pay", headers=auth_header(t2)
    ).status_code == 403
    assert client.post(
        f"/v1/orders/{order['order_id']}/cancel", headers=auth_header(t2)
    ).status_code == 403


def test_customer_views_own_orders(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=5)
    _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}])
    r = client.get("/v1/orders", headers=auth_header(customer_token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["items"][0]["product_name"] == "Laptop"


def test_admin_views_all_orders(client, admin_token):
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=5)
    register(client, "one@example.com", role="customer")
    t1 = login_token(client, "one@example.com")
    register(client, "two@example.com", role="customer")
    t2 = login_token(client, "two@example.com")
    _order(client, t1, [{"product_id": p["id"], "quantity": 1}])
    _order(client, t2, [{"product_id": p["id"], "quantity": 1}])

    r = client.get("/v1/admin/orders", headers=auth_header(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    # Admin response includes the customer block.
    customers = {o["customer"]["email"] for o in body["items"]}
    assert customers == {"one@example.com", "two@example.com"}


def test_admin_orders_filter_by_status(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=5)
    o1 = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    o2 = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    client.post(f"/v1/orders/{o1['order_id']}/pay", headers=auth_header(customer_token))

    paid = client.get(
        "/v1/admin/orders", headers=auth_header(admin_token), params={"status": "PAID"}
    ).json()
    created = client.get(
        "/v1/admin/orders", headers=auth_header(admin_token), params={"status": "CREATED"}
    ).json()
    assert paid["total"] == 1
    assert created["total"] == 1
