"""請求書 API のテスト。

受注からの作成、月締めのまとめ請求、発行、一部入金、訂正版、取消、受注の請求状況、自社情報の設定を確かめる。
変更はテストの最後に取り消される(conftest.py)。
"""

import re

import pytest

TENANT_A = "a1000000-0000-0000-0000-000000000001"  # サンプル商事: 登録番号あり・管理者
TENANT_B = "b2000000-0000-0000-0000-000000000002"  # テスト工業: 登録番号なし・一般
H = {"X-Tenant-Id": TENANT_A}
HB = {"X-Tenant-Id": TENANT_B}


def customer_id_of(client, name, headers=H):
    return next(c["id"] for c in client.get("/api/customers", headers=headers).json() if c["name"] == name)


@pytest.fixture(scope="module")
def setouchi(client):
    return customer_id_of(client, "株式会社みなと物産")


@pytest.fixture(scope="module")
def miyajima(client):
    return customer_id_of(client, "あさひ観光サービス")


def make_order(client, customer_id, lines, headers=H):
    body = {"customer_id": customer_id, "title": "保守契約", "order_date": "2026-10-01", "lines": lines}
    res = client.post("/api/orders", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def monthly_lines():
    """3か月分の保守(10月・11月・12月に納品)と、納品日がまだの1行。"""
    return [
        {"description": "保守 10月分", "quantity": "1", "unit": "月", "unit_price": "30000", "delivered_on": "2026-10-15"},
        {"description": "保守 11月分", "quantity": "1", "unit": "月", "unit_price": "30000", "delivered_on": "2026-11-10"},
        {"description": "保守 12月分", "quantity": "1", "unit": "月", "unit_price": "30000", "delivered_on": "2026-12-05"},
        {"description": "追加作業", "quantity": "1", "unit": "式", "unit_price": "8000"},
    ]


def issue(client, invoice_id, headers=H):
    res = client.post(f"/api/invoices/{invoice_id}/issue", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------
# 受注から作成・発行
# ---------------------------------------------------------------------
def test_受注から請求書を作って発行する(client, setouchi):
    order = make_order(client, setouchi, [
        {"description": "初期設定", "quantity": "1", "unit": "式", "unit_price": "100000", "delivered_on": "2026-11-05"},
        {"description": "お茶菓子", "quantity": "2", "unit": "箱", "unit_price": "1500", "tax_category": "reduced", "delivered_on": "2026-11-06"},
    ])
    res = client.post(f"/api/invoices/from-order/{order['id']}", headers=H)
    assert res.status_code == 201, res.text
    draft = res.json()
    assert draft["status"] == "draft"
    assert draft["invoice_number"] is None
    assert [l["transaction_date"] for l in draft["lines"]] == ["2026-11-05", "2026-11-06"]
    assert [l["order_line_id"] for l in draft["lines"]] == [l["line_id"] for l in order["lines"]]
    assert draft["orders"] == [{"id": order["id"], "order_number": order["order_number"]}]

    # 支払期限は作成中は空欄。発行時に、最も遅い取引日(11/6)の締め期間(月末締め・翌月末払い)から決まる
    assert draft["due_date"] is None
    assert draft["default_due_date"] == "2026-12-31"

    # 作成中の請求書がある受注は変更・削除できない
    order_now = client.get(f"/api/orders/{order['id']}", headers=H).json()
    assert len(order_now["invoices"]) == 1
    body = {"customer_id": setouchi, "lines": [{"description": "x", "quantity": "1", "unit_price": "1"}]}
    assert client.put(f"/api/orders/{order['id']}", json=body, headers=H).status_code == 409
    assert client.delete(f"/api/orders/{order['id']}", headers=H).status_code == 409

    # 発行: 番号・発行者情報のコピー・控えPDF
    invoice = issue(client, draft["id"])
    assert invoice["status"] == "issued"
    assert re.fullmatch(r"INV-\d{4}-\d{4}", invoice["invoice_number"])
    assert invoice["issuer_name"] == "株式会社サンプル商事"
    assert invoice["issuer_registration_number"] == "T1234567890123"
    assert invoice["due_date"] == "2026-12-31"
    assert (invoice["subtotal_amount"], invoice["tax_amount"], invoice["total_amount"]) == ("103000", "10240", "113240")
    assert [(s["tax_category"], s["tax_amount"]) for s in invoice["tax_summaries"]] == [("standard", "10000"), ("reduced", "240")]

    pdf = client.get(f"/api/invoices/{invoice['id']}/pdf", headers=H).content
    assert pdf.startswith(b"%PDF")
    assert client.get(f"/api/invoices/{invoice['id']}/pdf", headers=H).content == pdf

    # すべての明細を請求したので、受注は請求済になる
    order_now = client.get(f"/api/orders/{order['id']}", headers=H).json()
    assert order_now["status"] == "billed"
    assert order_now["unbilled_line_count"] == 0

    # 発行後は変更・削除できない
    invoice_body = {"customer_id": setouchi, "lines": [{"description": "x", "quantity": "1", "unit_price": "1", "transaction_date": "2026-11-05"}]}
    assert client.put(f"/api/invoices/{invoice['id']}", json=invoice_body, headers=H).status_code == 409
    assert client.delete(f"/api/invoices/{invoice['id']}", headers=H).status_code == 409


def test_作成中の請求書を削除すると受注明細はまた請求できる(client, setouchi):
    order = make_order(client, setouchi, monthly_lines()[:1])
    draft = client.post(f"/api/invoices/from-order/{order['id']}", headers=H).json()
    assert client.post(f"/api/invoices/from-order/{order['id']}", headers=H).status_code == 409  # 二重に作れない
    assert client.delete(f"/api/invoices/{draft['id']}", headers=H).status_code == 204
    assert client.post(f"/api/invoices/from-order/{order['id']}", headers=H).status_code == 201


# ---------------------------------------------------------------------
# 月締めのまとめ請求
# ---------------------------------------------------------------------
def test_締め請求は締め日までに納品した明細を集める(client, miyajima):
    order = make_order(client, miyajima, monthly_lines())
    other = make_order(client, miyajima, [
        {"description": "ガイドブック印刷", "quantity": "100", "unit": "部", "unit_price": "350", "delivered_on": "2026-11-20"},
    ])

    res = client.get("/api/invoices/billable-lines", params={"customer_id": miyajima, "closing_date": "2026-11-30"}, headers=H)
    assert res.status_code == 200
    billable = res.json()
    assert billable["period_start"] == "2026-11-01"
    assert billable["due_date"] == "2026-12-31"
    # 10月分(前の期間の請求漏れ)と11月分、別の受注の11月分。12月分と納品日なしは含まない
    assert [l["description"] for l in billable["lines"]] == ["保守 10月分", "保守 11月分", "ガイドブック印刷"]
    assert billable["missing_delivery_date"] == 1

    ids = [l["order_line_id"] for l in billable["lines"]]
    res = client.post("/api/invoices/from-orders", json={"customer_id": miyajima, "closing_date": "2026-11-30", "order_line_ids": ids}, headers=H)
    assert res.status_code == 201, res.text
    draft = res.json()
    assert draft["title"] == "2026年11月締め分"
    assert draft["due_date"] == "2026-12-31"
    assert [l["transaction_date"] for l in draft["lines"]] == ["2026-10-15", "2026-11-10", "2026-11-20"]
    assert {o["order_number"] for o in draft["orders"]} == {order["order_number"], other["order_number"]}

    # 同じ明細で二重に請求書は作れない
    again = client.post("/api/invoices/from-orders", json={"customer_id": miyajima, "order_line_ids": ids[:1]}, headers=H)
    assert again.status_code == 409

    issue(client, draft["id"])
    # 12月分と納品日なしが残るので、受注はまだ未請求。もう1つの受注は請求済
    first = client.get(f"/api/orders/{order['id']}", headers=H).json()
    assert (first["status"], first["unbilled_line_count"]) == ("open", 2)
    assert client.get(f"/api/orders/{other['id']}", headers=H).json()["status"] == "billed"

    # 12月締めで12月分を請求し、残りの1行は受注から請求すると、受注も請求済になる
    december = client.get("/api/invoices/billable-lines", params={"customer_id": miyajima, "closing_date": "2026-12-31"}, headers=H).json()
    assert [l["description"] for l in december["lines"]] == ["保守 12月分"]
    dec = client.post("/api/invoices/from-orders", json={"customer_id": miyajima, "closing_date": "2026-12-31",
                                                         "order_line_ids": [december["lines"][0]["order_line_id"]]}, headers=H).json()
    issue(client, dec["id"])
    rest = client.post(f"/api/invoices/from-order/{order['id']}", headers=H).json()
    assert [l["description"] for l in rest["lines"]] == ["追加作業"]
    assert rest["lines"][0]["transaction_date"] == "2026-10-01"  # 納品日も納期もないので受注日を仮に入れる
    assert rest["lines"][0]["date_confirmed"] is False
    assert client.post(f"/api/invoices/{rest['id']}/issue", headers=H).status_code == 409  # 仮の取引日のままでは発行できない
    line = rest["lines"][0]
    confirmed = {"customer_id": miyajima, "title": rest["title"], "lines": [{
        "description": line["description"], "quantity": line["quantity"], "unit": line["unit"], "unit_price": line["unit_price"],
        "tax_category": line["tax_category"], "order_line_id": line["order_line_id"],
        "transaction_date": "2026-12-10", "date_confirmed": True}]}
    assert client.put(f"/api/invoices/{rest['id']}", json=confirmed, headers=H).status_code == 200
    issue(client, rest["id"])
    assert client.get(f"/api/orders/{order['id']}", headers=H).json()["status"] == "billed"


def test_別の取引先の受注明細は請求書に載せられない(client, setouchi, miyajima):
    order = make_order(client, setouchi, monthly_lines()[:1])
    res = client.post("/api/invoices/from-orders", json={"customer_id": miyajima, "order_line_ids": [order["lines"][0]["line_id"]]}, headers=H)
    assert res.status_code in (409, 422)


# ---------------------------------------------------------------------
# 入金・取消
# ---------------------------------------------------------------------
def direct_invoice(client, customer_id, unit_price="100000", headers=H):
    body = {"customer_id": customer_id, "title": "コンサルティング",
            "lines": [{"description": "コンサルティング", "quantity": "1", "unit_price": unit_price, "transaction_date": "2026-11-12"}]}
    res = client.post("/api/invoices", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_一部入金と入金済(client, setouchi):
    invoice = issue(client, direct_invoice(client, setouchi)["id"])  # 110,000円
    pay = lambda amount: client.post(f"/api/invoices/{invoice['id']}/payments", json={"paid_on": "2026-12-10", "amount": amount}, headers=H)

    res = pay("50000")
    assert res.status_code == 201
    assert (res.json()["status"], res.json()["paid_amount"], res.json()["balance"]) == ("issued", "50000", "60000")

    assert pay("60001").status_code == 422  # 残額を超える入金

    res = pay("60000")
    assert (res.json()["status"], res.json()["balance"]) == ("paid", "0")
    assert any(i["id"] == invoice["id"] for i in client.get("/api/invoices", params={"filter": "paid"}, headers=H).json())

    # 入金を取り消すと発行済に戻る
    payment_id = res.json()["payments"][-1]["id"]
    res = client.delete(f"/api/invoices/{invoice['id']}/payments/{payment_id}", headers=H)
    assert (res.json()["status"], res.json()["balance"]) == ("issued", "60000")


def test_入金がある請求書は取り消せない_取り消すと受注は未請求に戻る(client, setouchi):
    order = make_order(client, setouchi, monthly_lines()[:1])
    invoice = issue(client, client.post(f"/api/invoices/from-order/{order['id']}", headers=H).json()["id"])
    assert client.get(f"/api/orders/{order['id']}", headers=H).json()["status"] == "billed"

    paid = client.post(f"/api/invoices/{invoice['id']}/payments", json={"paid_on": "2026-12-01", "amount": "1000"}, headers=H).json()
    assert client.post(f"/api/invoices/{invoice['id']}/void", headers=H).status_code == 409
    client.delete(f"/api/invoices/{invoice['id']}/payments/{paid['payments'][0]['id']}", headers=H)

    res = client.post(f"/api/invoices/{invoice['id']}/void", headers=H)
    assert res.status_code == 200
    assert res.json()["status"] == "void"
    assert client.get(f"/api/orders/{order['id']}", headers=H).json()["status"] == "open"
    # 取り消した請求書には入金できず、受注明細はもう一度請求できる
    assert client.post(f"/api/invoices/{invoice['id']}/payments", json={"paid_on": "2026-12-01", "amount": "1"}, headers=H).status_code == 409
    assert client.post(f"/api/invoices/from-order/{order['id']}", headers=H).status_code == 201


# ---------------------------------------------------------------------
# 訂正版
# ---------------------------------------------------------------------
def test_訂正版は同じ番号で版を上げ_旧版と控えが残る(client, setouchi):
    v1 = issue(client, direct_invoice(client, setouchi)["id"])
    v1_pdf = client.get(f"/api/invoices/{v1['id']}/pdf", headers=H).content
    client.post(f"/api/invoices/{v1['id']}/payments", json={"paid_on": "2026-12-10", "amount": "30000"}, headers=H)

    res = client.post(f"/api/invoices/{v1['id']}/corrections", headers=H)
    assert res.status_code == 201
    v2 = res.json()
    assert (v2["invoice_number"], v2["version"], v2["status"]) == (v1["invoice_number"], 2, "draft")
    assert client.post(f"/api/invoices/{v1['id']}/corrections", headers=H).status_code == 409  # 訂正版は1つずつ
    assert client.post(f"/api/invoices/{v1['id']}/void", headers=H).status_code == 409

    # 金額を直して発行
    body = {"customer_id": setouchi, "title": "コンサルティング",
            "lines": [{"description": "コンサルティング(訂正)", "quantity": "1", "unit_price": "90000", "transaction_date": "2026-11-12"}]}
    assert client.put(f"/api/invoices/{v2['id']}", json=body, headers=H).status_code == 200
    v2 = issue(client, v2["id"])
    assert v2["total_amount"] == "99000"
    assert v2["paid_amount"] == "30000"  # 入金は新しい版に付け替わる
    assert [v["version"] for v in v2["versions"]] == [1, 2]

    old = client.get(f"/api/invoices/{v1['id']}", headers=H).json()
    assert old["superseded_at"] is not None
    assert old["paid_amount"] == "0"
    assert client.get(f"/api/invoices/{v1['id']}/pdf", headers=H).content == v1_pdf  # 旧版の控えはそのまま

    # 一覧には最新版だけが出る
    listed = [i for i in client.get("/api/invoices", headers=H).json() if i["invoice_number"] == v1["invoice_number"]]
    assert [i["version"] for i in listed] == [2]


# ---------------------------------------------------------------------
# 登録番号のない会社・自社情報
# ---------------------------------------------------------------------
def test_登録番号のない会社の請求書(client):
    customer = customer_id_of(client, "ひかり精機株式会社", HB)
    invoice = issue(client, direct_invoice(client, customer, headers=HB)["id"], headers=HB)
    assert invoice["issuer_name"] == "テスト工業"
    assert invoice["issuer_registration_number"] is None


def test_取引日のない明細は登録できない(client, setouchi):
    body = {"customer_id": setouchi, "lines": [{"description": "作業", "quantity": "1", "unit_price": "100"}]}
    assert client.post("/api/invoices", json=body, headers=H).status_code == 422


def test_自社情報は管理者だけが変更できる(client):
    company = client.get("/api/company", headers=H).json()
    assert company["can_edit"] is True
    assert company["profile"]["registration_number"] == "T1234567890123"

    body = {"profile": {**company["profile"], "registration_number": "T123"}, "terms": company["terms"]}
    assert client.put("/api/company", json=body, headers=H).status_code == 422

    body["profile"]["registration_number"] = "Ｔ９８７６５４３２１０９８７"  # 全角でも受け付ける
    body["terms"] = {"closing_day": 20, "payment_month_offset": 1, "payment_day": 10}
    res = client.put("/api/company", json=body, headers=H)
    assert res.status_code == 200
    assert res.json()["profile"]["registration_number"] == "T9876543210987"
    assert res.json()["terms"]["closing_day"] == 20

    # 一般ユーザー(テスト工業)は見られるが、変更はできない
    company_b = client.get("/api/company", headers=HB).json()
    assert company_b["can_edit"] is False
    assert client.put("/api/company", json={**body}, headers=HB).status_code == 403

    # 元に戻す(以降のテストに影響させない)
    client.put("/api/company", json={"profile": company["profile"], "terms": company["terms"]}, headers=H)


def test_他社の請求書は見えない(client, setouchi):
    invoice = direct_invoice(client, setouchi)
    assert client.get(f"/api/invoices/{invoice['id']}", headers=HB).status_code == 404
    assert all(i["id"] != invoice["id"] for i in client.get("/api/invoices", headers=HB).json())


# ---------------------------------------------------------------------
# 論理チェックで見つけた問題の再発防止
# ---------------------------------------------------------------------
def test_一部請求した受注でも_残りの行の納品日を入力できる(client, setouchi):
    order = make_order(client, setouchi, [
        {"description": "保守 10月分", "quantity": "1", "unit_price": "30000", "delivered_on": "2026-10-31"},
        {"description": "保守 11月分", "quantity": "1", "unit_price": "30000"},
    ])
    october = client.get("/api/invoices/billable-lines", params={"customer_id": setouchi, "closing_date": "2026-10-31"}, headers=H).json()
    target = [l["order_line_id"] for l in october["lines"] if l["order_id"] == order["id"]]
    invoice = client.post("/api/invoices/from-orders", json={"customer_id": setouchi, "closing_date": "2026-10-31", "order_line_ids": target}, headers=H).json()
    issue(client, invoice["id"])

    order = client.get(f"/api/orders/{order['id']}", headers=H).json()
    assert [l["billed"] for l in order["lines"]] == [True, False]

    def body(first, second):
        ids = [l["line_id"] for l in order["lines"]]
        return {"customer_id": setouchi, "title": "保守契約", "order_date": "2026-10-01",
                "lines": [{**first, "line_id": ids[0]}, {**second, "line_id": ids[1]}]}

    billed_line = {"description": "保守 10月分", "quantity": "1", "unit_price": "30000", "delivered_on": "2026-10-31"}
    # 未請求の行には納品日を入れられる(金額も変えられる)
    res = client.put(f"/api/orders/{order['id']}", json=body(billed_line, {"description": "保守 11月分", "quantity": "1", "unit_price": "32000", "delivered_on": "2026-11-30"}), headers=H)
    assert res.status_code == 200, res.text
    assert res.json()["lines"][1]["delivered_on"] == "2026-11-30"
    assert res.json()["lines"][0]["line_id"] == order["lines"][0]["line_id"]  # 行IDは変わらない
    assert res.json()["total_amount"] == "68200"

    # 請求書に載った行は変更も削除もできない
    changed = client.put(f"/api/orders/{order['id']}", json=body({**billed_line, "unit_price": "1"}, {"description": "保守 11月分", "quantity": "1", "unit_price": "32000"}), headers=H)
    assert changed.status_code == 409
    removed = {"customer_id": setouchi, "order_date": "2026-10-01", "lines": [{"description": "新しい行", "quantity": "1", "unit_price": "100"}]}
    assert client.put(f"/api/orders/{order['id']}", json=removed, headers=H).status_code == 409

    # 納品日を入れた行は、次の締め請求の対象になる
    november = client.get("/api/invoices/billable-lines", params={"customer_id": setouchi, "closing_date": "2026-11-30"}, headers=H).json()
    assert order["lines"][1]["line_id"] in [l["order_line_id"] for l in november["lines"]]


def test_訂正で減額して過入金になったら返金を登録できる(client, setouchi):
    v1 = issue(client, direct_invoice(client, setouchi)["id"])  # 110,000円
    client.post(f"/api/invoices/{v1['id']}/payments", json={"paid_on": "2026-11-12", "amount": "110000"}, headers=H)
    v2 = client.post(f"/api/invoices/{v1['id']}/corrections", headers=H).json()
    body = {"customer_id": setouchi, "lines": [{"description": "コンサルティング", "quantity": "1", "unit_price": "90000", "transaction_date": "2026-11-12"}]}
    client.put(f"/api/invoices/{v2['id']}", json=body, headers=H)
    v2 = issue(client, v2["id"])
    assert (v2["total_amount"], v2["paid_amount"], v2["balance"]) == ("99000", "110000", "-11000")

    refund = lambda amount: client.post(f"/api/invoices/{v2['id']}/payments", json={"paid_on": "2026-11-13", "amount": amount, "memo": "過入金の返金"}, headers=H)
    assert refund("-11001").status_code == 422   # 過入金の額を超える返金
    assert refund("5000").status_code == 422     # 過入金のときに追加の入金はできない
    res = refund("-11000")
    assert res.status_code == 201
    assert (res.json()["status"], res.json()["balance"]) == ("paid", "0")

    # 過入金でない請求書には返金を登録できない
    other = issue(client, direct_invoice(client, setouchi)["id"])
    assert client.post(f"/api/invoices/{other['id']}/payments", json={"paid_on": "2026-11-13", "amount": "-1"}, headers=H).status_code == 422


def test_支払期限は最も遅い取引日の締め期間から決まる(client, setouchi):
    body = {"customer_id": setouchi, "lines": [
        {"description": "8月の作業", "quantity": "1", "unit_price": "1000", "transaction_date": "2026-08-20"},
        {"description": "9月の作業", "quantity": "1", "unit_price": "1000", "transaction_date": "2026-09-10"},
    ]}
    draft = client.post("/api/invoices", json=body, headers=H).json()
    assert draft["due_date"] is None
    assert draft["default_due_date"] == "2026-10-31"   # 9月末締め・翌月末払い(作成した日とは関係しない)
    assert issue(client, draft["id"])["due_date"] == "2026-10-31"

    # 手で入れた支払期限はそのまま使う
    manual = client.post("/api/invoices", json={**body, "due_date": "2026-12-15"}, headers=H).json()
    assert issue(client, manual["id"])["due_date"] == "2026-12-15"


def test_受注から作った行の数量と単価は請求書側で変えられない(client, setouchi):
    order = make_order(client, setouchi, [{"description": "部品", "quantity": "10", "unit": "個", "unit_price": "1000", "delivered_on": "2026-11-05"}])
    draft = client.post(f"/api/invoices/from-order/{order['id']}", headers=H).json()
    line = {"description": "部品", "quantity": "10", "unit": "個", "unit_price": "1000", "transaction_date": "2026-11-05",
            "order_line_id": draft["lines"][0]["order_line_id"]}
    put = lambda l: client.put(f"/api/invoices/{draft['id']}", json={"customer_id": setouchi, "lines": [l]}, headers=H)

    assert put({**line, "quantity": "4"}).status_code == 409
    assert put({**line, "unit_price": "900"}).status_code == 409
    # 品名・取引日は変えられる。行を外すこともできる(受注の行はまた請求できる状態に戻る)
    assert put({**line, "description": "部品(11月納品分)", "transaction_date": "2026-11-06"}).status_code == 200

    # 訂正版では、誤りを直すために金額も変えられる
    v1 = issue(client, draft["id"])
    v2 = client.post(f"/api/invoices/{v1['id']}/corrections", headers=H).json()
    res = client.put(f"/api/invoices/{v2['id']}", json={"customer_id": setouchi, "lines": [{**line, "unit_price": "900"}]}, headers=H)
    assert res.status_code == 200
