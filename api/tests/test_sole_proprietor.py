"""個人事業主への対応のテスト(事業形態・免税事業者・源泉徴収)。

変更はこのファイルのテストの最後に取り消される(conftest.py)。
"""

import pytest

TENANT_A = "a1000000-0000-0000-0000-000000000001"  # サンプル商事(管理者)
H = {"X-Tenant-Id": TENANT_A}


def company(client):
    return client.get("/api/company", headers=H).json()


def save_company(client, **profile):
    current = company(client)
    body = {"profile": {**current["profile"], **profile}, "terms": current["terms"]}
    return client.put("/api/company", json=body, headers=H)


@pytest.fixture
def sole_proprietor(client):
    """自社を個人事業主(登録番号あり・課税事業者)にする。テストの後で法人に戻す。"""
    original = company(client)["profile"]
    res = save_company(client, business_type="sole_proprietor", name="やまだデザイン室", representative_name="山田 太郎")
    assert res.status_code == 200, res.text
    yield
    save_company(client, **original)


def new_customer(client, name, withholding_default=False):
    res = client.post("/api/customers", json={"name": name, "withholding_default": withholding_default}, headers=H)
    assert res.status_code == 201
    return res.json()["id"]


def line(price="100000", date="2026-11-20"):
    return {"description": "デザイン料", "quantity": "1", "unit_price": price, "transaction_date": date}


def test_登録番号があると免税事業者にできない(client):
    res = save_company(client, tax_exempt=True)  # サンプル商事は登録番号あり
    assert res.status_code == 422
    assert "登録番号" in res.text


def test_法人では源泉徴収を指定できない(client):
    c = new_customer(client, "源泉テスト(法人)", withholding_default=True)
    res = client.post("/api/invoices", json={"customer_id": c, "withholding": True, "lines": [line()]}, headers=H)
    assert res.status_code == 422
    # 取引先の初期値が源泉ありでも、法人の請求書には源泉をかけない
    draft = client.post("/api/invoices", json={"customer_id": c, "lines": [line()]}, headers=H).json()
    assert (draft["withholding"], draft["withholding_amount"]) == (False, "0")


def test_源泉徴収した請求書は差引ご請求額で入金を管理する(client, sole_proprietor):
    c = new_customer(client, "源泉テスト株式会社", withholding_default=True)
    draft = client.post("/api/invoices", json={"customer_id": c, "lines": [line("100000")]}, headers=H).json()
    # 取引先の初期値で源泉徴収あり。税抜 100,000円 × 10.21% = 10,210円
    assert (draft["withholding"], draft["withholding_amount"]) == (True, "10210")
    assert (draft["total_amount"], draft["net_amount"], draft["balance"]) == ("110000", "99790", "99790")

    invoice = client.post(f"/api/invoices/{draft['id']}/issue", headers=H).json()
    assert invoice["issuer_name"] == "やまだデザイン室"
    assert invoice["issuer_representative"] == "山田 太郎"
    assert client.get(f"/api/invoices/{invoice['id']}/pdf", headers=H).content.startswith(b"%PDF")

    # 差引ご請求額を超える入金は受け付けず、ちょうど入金すると入金済になる
    pay = lambda amount: client.post(f"/api/invoices/{invoice['id']}/payments", json={"paid_on": "2026-12-20", "amount": amount}, headers=H)
    assert pay("99791").status_code == 422
    res = pay("99790")
    assert (res.json()["status"], res.json()["balance"]) == ("paid", "0")

    # 源泉徴収を外すと、源泉徴収税額は 0 に戻る
    draft2 = client.post("/api/invoices", json={"customer_id": c, "lines": [line("100000")]}, headers=H).json()
    res = client.put(f"/api/invoices/{draft2['id']}", json={"customer_id": c, "withholding": False, "lines": [line("100000")]}, headers=H)
    assert (res.json()["withholding"], res.json()["withholding_amount"], res.json()["net_amount"]) == (False, "0", "110000")


def test_源泉徴収はまとめ入金と繰越でも差引額を使う(client, sole_proprietor):
    c = new_customer(client, "源泉まとめ入金テスト", withholding_default=True)

    def closing(closing_date, delivered_on, price):
        order = client.post("/api/orders", json={"customer_id": c, "order_date": delivered_on, "lines": [
            {"description": "制作", "quantity": "1", "unit_price": price, "delivered_on": delivered_on}]}, headers=H).json()
        d = client.post("/api/invoices/from-orders", json={"customer_id": c, "closing_date": closing_date,
                                                           "order_line_ids": [order["lines"][0]["line_id"]]}, headers=H).json()
        return client.post(f"/api/invoices/{d['id']}/issue", headers=H).json()

    oct_ = closing("2026-10-31", "2026-10-10", "100000")   # 110,000 − 10,210 = 99,790
    assert oct_["net_amount"] == "99790"
    nov = closing("2026-11-30", "2026-11-10", "50000")      # 55,000 − 5,105 = 49,895
    assert (nov["carried_over_amount"], nov["amount_due"]) == ("99790", "149685")

    open_ = client.get("/api/receipts/open-invoices", params={"customer_id": c}, headers=H).json()
    assert open_["total_balance"] == "149685"
    receipt = client.post("/api/receipts", json={"customer_id": c, "received_on": "2026-12-10", "amount": "149685"}, headers=H).json()
    assert [a["balance_after"] for a in receipt["allocations"]] == ["0", "0"]
    assert client.get(f"/api/invoices/{nov['id']}", headers=H).json()["status"] == "paid"


def test_免税事業者は消費税を請求しない(client, sole_proprietor):
    assert save_company(client, registration_number=None, tax_exempt=True).status_code == 200
    c = new_customer(client, "免税テスト商店")

    calc = client.post("/api/calculate", json={"lines": [{"quantity": "1", "unit_price": "100000", "tax_category": "standard"}],
                                               "withholding": True}, headers=H).json()
    assert (calc["subtotal_amount"], calc["tax_amount"], calc["total_amount"]) == ("100000", "0", "100000")
    assert (calc["withholding_amount"], calc["net_amount"]) == ("10210", "89790")

    quote = client.post("/api/quotes", json={"customer_id": c, "lines": [{"description": "制作", "quantity": "1", "unit_price": "100000"}]}, headers=H).json()
    assert (quote["tax_amount"], quote["total_amount"]) == ("0", "100000")
    assert client.get(f"/api/quotes/{quote['id']}/pdf", headers=H).content.startswith(b"%PDF")

    invoice = client.post("/api/invoices", json={"customer_id": c, "lines": [line("100000")]}, headers=H).json()
    invoice = client.post(f"/api/invoices/{invoice['id']}/issue", headers=H).json()
    assert (invoice["tax_amount"], invoice["total_amount"]) == ("0", "100000")
    assert invoice["issuer_tax_exempt"] is True
    assert invoice["issuer_registration_number"] is None
