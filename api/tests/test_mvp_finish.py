"""MVP の仕上げで直した点のテスト。変更はテストの最後に取り消される(conftest.py)。

- 見積から受注への変換で、金額を計算し直さずに引き継ぐ
- 受注から作った請求書の仮の取引日は、確認するまで発行できない
- 発行済み書類の検索(取引年月日・金額・取引先)と、控えの改ざんチェック
"""

from pathlib import Path

from app.config import settings

TENANT_A = "a1000000-0000-0000-0000-000000000001"
TENANT_B = "b2000000-0000-0000-0000-000000000002"
H = {"X-Tenant-Id": TENANT_A}


def customer(client, name):
    return client.post("/api/customers", json={"name": name}, headers=H).json()["id"]


def set_rounding(client, mode):
    conn = client.db_conn
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        conn.execute("UPDATE app.tenant_settings SET rounding_mode = %s", (mode,))


def test_見積の金額をそのまま受注に引き継ぐ(client):
    c = customer(client, "引き継ぎテスト")
    quote = client.post("/api/quotes", json={"customer_id": c, "lines": [
        {"description": "作業", "quantity": "1", "unit_price": "1005"}]}, headers=H).json()
    quote = client.post(f"/api/quotes/{quote['id']}/issue", headers=H).json()
    assert quote["total_amount"] == "1105"  # 切り捨て: 1005 + 100

    set_rounding(client, "ceil")  # 見積の発行後に端数処理の設定を変える
    try:
        order = client.post(f"/api/orders/from-quote/{quote['id']}", headers=H).json()
        assert (order["subtotal_amount"], order["tax_amount"], order["total_amount"]) == ("1005", "100", "1105")
        assert order["lines"][0]["amount"] == "1005"
        assert [s["tax_amount"] for s in order["tax_summaries"]] == ["100"]
    finally:
        set_rounding(client, "floor")


def test_仮の取引日は確認するまで発行できない(client):
    c = customer(client, "仮の取引日テスト")
    order = client.post("/api/orders", json={"customer_id": c, "order_date": "2026-11-01", "lines": [
        {"description": "納品済み", "quantity": "1", "unit_price": "1000", "delivered_on": "2026-11-05"},
        {"description": "納品日なし", "quantity": "1", "unit_price": "2000"}]}, headers=H).json()
    draft = client.post(f"/api/invoices/from-order/{order['id']}", headers=H).json()
    assert [l["date_confirmed"] for l in draft["lines"]] == [True, False]

    res = client.post(f"/api/invoices/{draft['id']}/issue", headers=H)
    assert res.status_code == 409
    assert "2行目" in res.json()["detail"]

    # 日付を確かめて確定すれば発行できる
    lines = [{k: l[k] for k in ("description", "quantity", "unit", "unit_price", "tax_category", "order_line_id", "transaction_date")}
             | {"date_confirmed": True} for l in draft["lines"]]
    assert client.put(f"/api/invoices/{draft['id']}", json={"customer_id": c, "lines": lines}, headers=H).status_code == 200
    assert client.post(f"/api/invoices/{draft['id']}/issue", headers=H).status_code == 200


def test_発行済み書類を条件で検索できる(client):
    c = customer(client, "検索テスト株式会社")
    quote = client.post("/api/quotes", json={"customer_id": c, "lines": [
        {"description": "見積の作業", "quantity": "1", "unit_price": "12345"}]}, headers=H).json()
    quote = client.post(f"/api/quotes/{quote['id']}/issue", headers=H).json()  # 13,579円
    inv = client.post("/api/invoices", json={"customer_id": c, "lines": [
        {"description": "請求の作業", "quantity": "1", "unit_price": "50000", "transaction_date": "2026-11-10"}]}, headers=H).json()
    inv = client.post(f"/api/invoices/{inv['id']}/issue", headers=H).json()  # 55,000円

    search = lambda **q: client.get("/api/documents", params=q, headers=H).json()

    found = search(customer="検索テスト")
    assert {d["document_number"] for d in found} == {quote["quote_number"], inv["invoice_number"]}
    assert search(customer="検索テスト", doc_type="invoice")[0]["document_id"] == inv["id"]
    # 金額の範囲と、取引先の組み合わせ
    assert [d["total_amount"] for d in search(customer="検索テスト", amount_min="50000", amount_max="60000")] == ["55000"]
    assert search(customer="検索テスト", amount_max="1000") == []
    # 取引年月日の範囲(発行日)
    issued_on = inv["issue_date"]
    assert len(search(customer="検索テスト", date_from=issued_on, date_to=issued_on)) == 2
    assert search(customer="検索テスト", date_to="2000-01-01") == []
    # 全角で入力しても、記号を含んでいても検索できる
    assert len(search(number=inv["invoice_number"].replace("INV", "ＩＮＶ"))) == 1
    assert search(customer="%") == [] or all("%" in d["customer_name"] for d in search(customer="%"))
    # 範囲が逆なら 422
    assert client.get("/api/documents", params={"amount_min": "10", "amount_max": "1"}, headers=H).status_code == 422
    # 他社からは見えない
    assert client.get("/api/documents", params={"customer": "検索テスト"}, headers={"X-Tenant-Id": TENANT_B}).json() == []


def test_控えの改ざんを検出できる(client):
    c = customer(client, "改ざんテスト")
    inv = client.post("/api/invoices", json={"customer_id": c, "lines": [
        {"description": "作業", "quantity": "1", "unit_price": "1000", "transaction_date": "2026-11-10"}]}, headers=H).json()
    client.post(f"/api/invoices/{inv['id']}/issue", headers=H)
    doc = client.get("/api/documents", params={"customer": "改ざんテスト"}, headers=H).json()[0]

    assert client.get(f"/api/documents/{doc['id']}/pdf", headers=H).content.startswith(b"%PDF")
    ok = client.get(f"/api/documents/{doc['id']}/verify", headers=H).json()
    assert ok["ok"] is True

    # 保存しているファイルを書き換えると、一致しなくなる
    conn = client.db_conn
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        path = conn.execute("SELECT storage_path FROM app.issued_documents WHERE id = %s", (doc["id"],)).fetchone()["storage_path"]
    file = Path(settings.document_dir, *path.split("/"))
    file.write_bytes(file.read_bytes() + b"tampered")
    ng = client.get(f"/api/documents/{doc['id']}/verify", headers=H).json()
    assert ng["ok"] is False
