"""締め請求の繰越と、取引先ごとのまとめ入金のテスト。変更はテストの最後に取り消される(conftest.py)。"""

import pytest

TENANT_A = "a1000000-0000-0000-0000-000000000001"
TENANT_B = "b2000000-0000-0000-0000-000000000002"
H = {"X-Tenant-Id": TENANT_A}


@pytest.fixture(scope="module")
def customer(client):
    """このテスト専用の取引先(他のテストの請求書と混ざらないようにする)。"""
    res = client.post("/api/customers", json={"name": "繰越テスト商会"}, headers=H)
    assert res.status_code == 201
    return res.json()["id"]


def closing_invoice(client, customer_id, closing_date, delivered_on, unit_price):
    """受注1行を作り、その月の締め請求として発行する。"""
    order = client.post("/api/orders", json={"customer_id": customer_id, "order_date": delivered_on, "lines": [
        {"description": f"{delivered_on[:7]} 作業", "quantity": "1", "unit_price": unit_price, "delivered_on": delivered_on}]}, headers=H).json()
    ids = [l["line_id"] for l in order["lines"]]
    draft = client.post("/api/invoices/from-orders", json={"customer_id": customer_id, "closing_date": closing_date, "order_line_ids": ids}, headers=H).json()
    return draft, client.post(f"/api/invoices/{draft['id']}/issue", headers=H).json()


def test_締め請求に前回までの未入金が繰越として載る(client, customer):
    _, oct_ = closing_invoice(client, customer, "2026-10-31", "2026-10-20", "50000")   # 55,000円
    assert oct_["closing_date"] == "2026-10-31"
    assert (oct_["carried_over_amount"], oct_["amount_due"]) == ("0", "55000")

    # 10月分に一部入金(20,000円)
    client.post(f"/api/invoices/{oct_['id']}/payments", json={"paid_on": "2026-11-10", "amount": "20000"}, headers=H)

    draft, nov = closing_invoice(client, customer, "2026-11-30", "2026-11-20", "30000")  # 33,000円
    # 作成中から見込みが出る
    assert (draft["carried_over_amount"], draft["amount_due"]) == ("35000", "68000")
    # 発行時に確定し、内訳が残る
    assert (nov["carried_over_amount"], nov["total_amount"], nov["amount_due"]) == ("35000", "33000", "68000")
    assert [(c["source_number"], c["balance"]) for c in nov["carryovers"]] == [(oct_["invoice_number"], "35000")]
    # 繰越は請求書の金額(インボイス)には含めない。この請求書自体の残額は今回の取引分だけ
    assert nov["balance"] == "33000"
    assert [s["taxable_amount"] for s in nov["tax_summaries"]] == ["30000"]

    # あとで10月分が入金されても、発行済みの繰越は変わらない(書類の写しとして固定)
    client.post(f"/api/invoices/{oct_['id']}/payments", json={"paid_on": "2026-12-01", "amount": "35000"}, headers=H)
    again = client.get(f"/api/invoices/{nov['id']}", headers=H).json()
    assert again["carried_over_amount"] == "35000"

    # PDF には繰越を載せる(生成できること)
    assert client.get(f"/api/invoices/{nov['id']}/pdf", headers=H).content.startswith(b"%PDF")


def test_受注から作った請求書には繰越を載せない(client, customer):
    order = client.post("/api/orders", json={"customer_id": customer, "order_date": "2026-11-05", "lines": [
        {"description": "単発作業", "quantity": "1", "unit_price": "10000", "delivered_on": "2026-11-05"}]}, headers=H).json()
    draft = client.post(f"/api/invoices/from-order/{order['id']}", headers=H).json()
    assert draft["closing_date"] is None
    assert draft["carried_over_amount"] == "0"
    assert draft["carryovers"] == []


def test_まとめ入金は古い請求書から順に割り当てる(client):
    cust = client.post("/api/customers", json={"name": "まとめ入金テスト"}, headers=H).json()["id"]
    _, a = closing_invoice(client, cust, "2026-09-30", "2026-09-10", "10000")  # 11,000円
    _, b = closing_invoice(client, cust, "2026-10-31", "2026-10-10", "20000")  # 22,000円
    _, c = closing_invoice(client, cust, "2026-11-30", "2026-11-10", "30000")  # 33,000円

    open_ = client.get("/api/receipts/open-invoices", params={"customer_id": cust}, headers=H).json()
    assert open_["total_balance"] == "66000"
    assert [i["invoice_id"] for i in open_["invoices"]] == [a["id"], b["id"], c["id"]]

    body = {"customer_id": cust, "received_on": "2026-12-10", "amount": "40000", "memo": "11月末振込"}
    # 確認だけ(登録しない)
    preview = client.post("/api/receipts", json={**body, "dry_run": True}, headers=H).json()
    assert preview["id"] is None
    assert [(x["invoice_id"], x["amount"], x["balance_after"]) for x in preview["allocations"]] == [
        (a["id"], "11000", "0"), (b["id"], "22000", "0"), (c["id"], "7000", "26000")]
    assert client.get(f"/api/invoices/{a['id']}", headers=H).json()["status"] == "issued"

    # 登録
    receipt = client.post("/api/receipts", json=body, headers=H).json()
    assert receipt["id"] is not None
    states = [client.get(f"/api/invoices/{i['id']}", headers=H).json() for i in (a, b, c)]
    assert [(s["status"], s["balance"]) for s in states] == [("paid", "0"), ("paid", "0"), ("issued", "26000")]
    assert states[2]["payments"][0]["receipt_id"] == receipt["id"]

    # 割り当てた入金は請求書の画面からは消せない(まとめ入金ごと取り消す)
    pid = states[2]["payments"][0]["id"]
    assert client.delete(f"/api/invoices/{c['id']}/payments/{pid}", headers=H).status_code == 409

    # 未入金の合計を超える入金は受け付けない
    over = client.post("/api/receipts", json={**body, "amount": "26001"}, headers=H)
    assert over.status_code == 422

    # 取り消すと、すべての請求書が元に戻る
    assert len(client.get("/api/receipts", params={"customer_id": cust}, headers=H).json()) == 1
    assert client.delete(f"/api/receipts/{receipt['id']}", headers=H).status_code == 204
    states = [client.get(f"/api/invoices/{i['id']}", headers=H).json() for i in (a, b, c)]
    assert [(s["status"], s["balance"]) for s in states] == [("issued", "11000"), ("issued", "22000"), ("issued", "33000")]
    assert client.get("/api/receipts", params={"customer_id": cust}, headers=H).json() == []


def test_まとめ入金は他社から見えない(client):
    cust = client.post("/api/customers", json={"name": "分離テスト"}, headers=H).json()["id"]
    closing_invoice(client, cust, "2026-10-31", "2026-10-10", "1000")
    r = client.post("/api/receipts", json={"customer_id": cust, "received_on": "2026-11-01", "amount": "1100"}, headers=H).json()
    other = {"X-Tenant-Id": TENANT_B}
    assert client.delete(f"/api/receipts/{r['id']}", headers=other).status_code == 404
    assert client.get("/api/receipts", params={"customer_id": cust}, headers=other).json() == []
