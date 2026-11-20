"""受注 API のテスト。見積からの変換、直接作成、変更・削除、注文請書、請求済みの保護を確かめる。

変更はテストの最後に取り消される(conftest.py)。
"""

import re

import psycopg
import pytest

TENANT_A = "a1000000-0000-0000-0000-000000000001"
TENANT_B = "b2000000-0000-0000-0000-000000000002"
H = {"X-Tenant-Id": TENANT_A}


@pytest.fixture(scope="module")
def customer_id(client):
    customers = client.get("/api/customers", headers=H).json()
    return next(c["id"] for c in customers if c["name"] == "株式会社みなと物産")


def quote_lines():
    return [
        {"description": "初期設定", "quantity": "1", "unit": "式", "unit_price": "100000", "tax_category": "standard"},
        {"description": "お茶菓子", "quantity": "2", "unit": "箱", "unit_price": "1500", "tax_category": "reduced"},
    ]


def issued_quote(client, customer_id) -> dict:
    quote = client.post("/api/quotes", json={"customer_id": customer_id, "title": "導入一式", "remarks": "月末締め", "lines": quote_lines()}, headers=H).json()
    return client.post(f"/api/quotes/{quote['id']}/issue", headers=H).json()


def order_body(customer_id, **overrides):
    body = {
        "customer_id": customer_id,
        "title": "保守契約",
        "order_date": "2026-11-12",
        "delivery_due_date": "2026-11-30",
        "lines": [{"description": "保守(１２月分)", "quantity": "1", "unit": "月", "unit_price": "30000", "delivered_on": "2026-12-31"}],
    }
    return {**body, **overrides}


def mark_billed(client, order_id):
    """請求機能はまだないので、DB を直接更新して請求済みにする。"""
    conn = client.db_conn
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        conn.execute("UPDATE app.orders SET status = 'billed' WHERE id = %s", (order_id,))


def test_見積から受注に変換する(client, customer_id):
    quote = issued_quote(client, customer_id)
    res = client.post(f"/api/orders/from-quote/{quote['id']}", headers=H)
    assert res.status_code == 201
    order = res.json()

    assert re.fullmatch(r"O-\d{4}-\d{4}", order["order_number"])
    assert order["status"] == "open"
    assert order["quote_id"] == quote["id"]
    assert order["quote_number"] == quote["quote_number"]
    assert (order["customer_name"], order["title"], order["remarks"]) == ("株式会社みなと物産", "導入一式", "月末締め")
    assert order["total_amount"] == quote["total_amount"]
    assert [l["quote_line_id"] for l in order["lines"]] == [l["line_id"] for l in quote["lines"]]

    # 見積は受注済みになり、受注へのリンクを持つ
    quote = client.get(f"/api/quotes/{quote['id']}", headers=H).json()
    assert quote["status"] == "ordered"
    assert quote["order_id"] == order["id"]

    # 2回目は変換できない
    assert client.post(f"/api/orders/from-quote/{quote['id']}", headers=H).status_code == 409


def test_作成中の見積は受注に変換できない(client, customer_id):
    draft = client.post("/api/quotes", json={"customer_id": customer_id, "lines": quote_lines()}, headers=H).json()
    assert client.post(f"/api/orders/from-quote/{draft['id']}", headers=H).status_code == 409


def test_見積なしで受注を登録し変更する(client, customer_id):
    res = client.post("/api/orders", json=order_body(customer_id), headers=H)
    assert res.status_code == 201
    order = res.json()
    assert order["quote_id"] is None
    assert order["order_date"] == "2026-11-12"
    assert order["lines"][0]["description"] == "保守(12月分)"  # 全角数字は半角に
    assert order["lines"][0]["delivered_on"] == "2026-12-31"
    assert order["total_amount"] == "33000"

    # 未請求なら明細・金額も変えられる
    changed = order_body(customer_id, lines=[
        {"description": "保守(12月分)", "quantity": "1", "unit": "月", "unit_price": "40000"},
        {"description": "出張費", "quantity": "1", "unit_price": "5000"},
    ])
    res = client.put(f"/api/orders/{order['id']}", json=changed, headers=H)
    assert res.status_code == 200
    assert res.json()["total_amount"] == "49500"
    assert len(res.json()["lines"]) == 2


def test_受注を削除すると見積は発行済みに戻る(client, customer_id):
    quote = issued_quote(client, customer_id)
    order = client.post(f"/api/orders/from-quote/{quote['id']}", headers=H).json()
    assert client.delete(f"/api/orders/{order['id']}", headers=H).status_code == 204
    assert client.get(f"/api/orders/{order['id']}", headers=H).status_code == 404

    quote = client.get(f"/api/quotes/{quote['id']}", headers=H).json()
    assert quote["status"] == "issued"
    assert quote["order_id"] is None
    # 戻した見積はもう一度変換できる
    assert client.post(f"/api/orders/from-quote/{quote['id']}", headers=H).status_code == 201


def test_注文請書の発行とプレビュー(client, customer_id):
    order = client.post("/api/orders", json=order_body(customer_id), headers=H).json()

    # 発行前: プレビューは出せるが、控えはまだない
    preview = client.get(f"/api/orders/{order['id']}/acknowledgment/pdf", params={"preview": "true"}, headers=H)
    assert preview.status_code == 200 and preview.content.startswith(b"%PDF")
    assert client.get(f"/api/orders/{order['id']}/acknowledgment/pdf", headers=H).status_code == 404

    # 発行するたびに版が上がり、控えが残る
    first = client.post(f"/api/orders/{order['id']}/acknowledgment", headers=H).json()
    assert [a["version"] for a in first["acknowledgments"]] == [1]
    v1 = client.get(f"/api/orders/{order['id']}/acknowledgment/pdf", headers=H).content

    client.put(f"/api/orders/{order['id']}", json=order_body(customer_id, title="保守契約(変更後)"), headers=H)
    second = client.post(f"/api/orders/{order['id']}/acknowledgment", headers=H).json()
    assert [a["version"] for a in second["acknowledgments"]] == [1, 2]

    latest = client.get(f"/api/orders/{order['id']}/acknowledgment/pdf", headers=H).content
    old = client.get(f"/api/orders/{order['id']}/acknowledgment/pdf", params={"version": 1}, headers=H).content
    assert old == v1
    assert latest != v1

    # 注文請書を出した受注は削除できない
    assert client.delete(f"/api/orders/{order['id']}", headers=H).status_code == 409


def test_請求済みの受注は変更も削除もできない(client, customer_id):
    order = client.post("/api/orders", json=order_body(customer_id), headers=H).json()
    mark_billed(client, order["id"])

    assert client.put(f"/api/orders/{order['id']}", json=order_body(customer_id), headers=H).status_code == 409
    assert client.delete(f"/api/orders/{order['id']}", headers=H).status_code == 409

    # API を通さずに DB を直接変えようとしても、トリガーが拒否する
    conn = client.db_conn
    with pytest.raises(psycopg.errors.RaiseException):
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            conn.execute("UPDATE app.order_lines SET unit_price = 1 WHERE order_id = %s", (order["id"],))

    billed = client.get("/api/orders", params={"filter": "billed"}, headers=H).json()
    assert any(o["id"] == order["id"] for o in billed)


def test_他社の受注は見えない(client, customer_id):
    order = client.post("/api/orders", json=order_body(customer_id), headers=H).json()
    other = {"X-Tenant-Id": TENANT_B}
    assert client.get(f"/api/orders/{order['id']}", headers=other).status_code == 404
    assert all(o["id"] != order["id"] for o in client.get("/api/orders", headers=other).json())
