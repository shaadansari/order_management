"""Order flow tests — see design §11.

Stock model: stock is NOT touched at order creation (an order is a price-snapped
intent). It is reduced atomically only on successful payment, which is also where the
oversell / race-condition guard now lives.
"""
from .conftest import auth_header, make_product, register, login_token


def _order(client, token, items):
    return client.post("/v1/orders", headers=auth_header(token), json={"items": items})


def _my_first_item_stock(client, token):
    """current_stock of the most recent order's first line — the LIVE product stock."""
    orders = client.get("/v1/orders", headers=auth_header(token)).json()
    return orders["items"][0]["items"][0]["current_stock"]


# --------------------------------------------------------------------------- #
# Creation — stock is NOT reduced here
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
    assert item["current_stock"] == 10    # stock UNCHANGED at creation (reduced on payment)


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


def test_create_order_does_not_reduce_stock(client, admin_token, customer_token):
    # Ordering MORE than stock now succeeds at creation — stock is neither checked nor
    # decremented here. current_stock reflects the full, unchanged stock.
    p = make_product(client, admin_token, name="Laptop", stock=3)
    r = _order(client, customer_token, [{"product_id": p["id"], "quantity": 5}])
    assert r.status_code == 201
    assert r.json()["items"][0]["current_stock"] == 3  # unchanged


def test_create_order_unavailable_product_400(client, admin_token, customer_token):
    # Stock aside, an unavailable (soft-deleted) product still can't be ordered.
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=5, available=False)
    r = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}])
    assert r.status_code == 400
    assert r.json()["error"] == "PRODUCT_UNAVAILABLE"


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
# Pay — stock is reduced here, atomically
# --------------------------------------------------------------------------- #
def test_pay_order_marks_paid_and_reduces_stock(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    # Stock is full before payment.
    assert order["items"][0]["current_stock"] == 5
    r = client.post(f"/v1/orders/{order['order_id']}/pay", headers=auth_header(customer_token))
    assert r.status_code == 200
    assert r.json()["status"] == "PAID"
    # Stock is reduced only on successful payment: 5 -> 4.
    assert _my_first_item_stock(client, customer_token) == 4


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


def test_pay_insufficient_stock_400(client, admin_token, customer_token):
    # Creating more than stock succeeds; the oversell guard fires at payment time.
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=3)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 5}])
    assert order.status_code == 201
    r = client.post(
        f"/v1/orders/{order.json()['order_id']}/pay", headers=auth_header(customer_token)
    )
    assert r.status_code == 400
    assert r.json()["error"] == "INSUFFICIENT_STOCK"
    # Order stays CREATED and stock is untouched (the reservation rolled back).
    mine = client.get("/v1/orders", headers=auth_header(customer_token)).json()
    assert mine["items"][0]["status"] == "CREATED"
    assert mine["items"][0]["items"][0]["current_stock"] == 3


def test_payment_failure_returns_402_and_leaves_created(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 1}]).json()
    # force_fail simulates a declined SIMULATED payment (the 402 path).
    r = client.post(
        f"/v1/orders/{order['order_id']}/pay",
        headers=auth_header(customer_token),
        params={"force_fail": "true"},
    )
    assert r.status_code == 402
    assert r.json()["error"] == "PAYMENT_FAILED"
    # Order remains CREATED after a failed payment, and stock is NOT reduced (rolled back).
    mine = client.get("/v1/orders", headers=auth_header(customer_token)).json()
    assert mine["items"][0]["status"] == "CREATED"
    assert mine["items"][0]["items"][0]["current_stock"] == 5


def test_concurrent_payments_for_last_item_only_one_succeeds(client, admin_token):
    # One item left. Two customers each create an order for it (both CREATED — no stock
    # held at creation), then both pay. The atomic UPDATE + rowcount guard ensures only
    # one payment wins; the other gets INSUFFICIENT_STOCK. (SQLite serializes writers, so
    # the two pays run sequentially here — the atomic statement itself is the real
    # guarantee; this test exercises that exact code path.)
    p = make_product(client, admin_token, name="Rare", price=5.00, stock=1)
    register(client, "a@example.com", role="customer")
    ta = login_token(client, "a@example.com")
    register(client, "b@example.com", role="customer")
    tb = login_token(client, "b@example.com")

    oa = _order(client, ta, [{"product_id": p["id"], "quantity": 1}]).json()
    ob = _order(client, tb, [{"product_id": p["id"], "quantity": 1}]).json()

    ra = client.post(f"/v1/orders/{oa['order_id']}/pay", headers=auth_header(ta))
    rb = client.post(f"/v1/orders/{ob['order_id']}/pay", headers=auth_header(tb))

    assert {ra.status_code, rb.status_code} == {200, 400}
    bodies = [ra.json(), rb.json()]
    assert [b for b in bodies if b.get("status") == "PAID"]  # exactly one paid
    assert [b for b in bodies if b.get("error") == "INSUFFICIENT_STOCK"]  # exactly one refused


# --------------------------------------------------------------------------- #
# Cancel — stock is NOT touched (nothing was ever reserved)
# --------------------------------------------------------------------------- #
def test_cancel_order_marks_cancelled(client, admin_token, customer_token):
    p = make_product(client, admin_token, name="Laptop", price=100.00, stock=5)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 2}]).json()
    r = client.post(f"/v1/orders/{order['order_id']}/cancel", headers=auth_header(customer_token))
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


def test_cancel_does_not_touch_stock(client, admin_token, customer_token):
    # A CREATED order holds no stock, so cancelling must not change availability. Create
    # (stock unchanged at 2), cancel, then prove the stock is still fully there by
    # ordering the same quantity again.
    p = make_product(client, admin_token, name="Laptop", price=10.00, stock=2)
    order = _order(client, customer_token, [{"product_id": p["id"], "quantity": 2}]).json()
    assert order["items"][0]["current_stock"] == 2  # not reserved at creation
    r = client.post(f"/v1/orders/{order['order_id']}/cancel", headers=auth_header(customer_token))
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    again = _order(client, customer_token, [{"product_id": p["id"], "quantity": 2}])
    assert again.status_code == 201
    assert again.json()["items"][0]["current_stock"] == 2  # still full


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
