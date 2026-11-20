"""マスタ(取引先・商品・サービス)と、データの一括エクスポートのテスト。

変更はこのファイルのテストの最後に取り消される(conftest.py)。
"""

import csv
import hashlib
import io
import zipfile

TENANT_A = "a1000000-0000-0000-0000-000000000001"  # サンプル商事(管理者)
TENANT_B = "b2000000-0000-0000-0000-000000000002"  # テスト工業(一般の利用者)
H = {"X-Tenant-Id": TENANT_A}
HB = {"X-Tenant-Id": TENANT_B}

TERMS_20_10 = {"closing_day": 20, "payment_month_offset": 1, "payment_day": 10}  # 20日締め翌月10日払い


def customer_body(**kw):
    return {"name": "マスタテスト商会", "honorific": "御中", "contact_name": None, "address": None, "email": None,
            "withholding_default": False, "terms": None, "is_active": True, **kw}


# ---------------------------------------------------------------------
# 取引先
# ---------------------------------------------------------------------
def test_取引先の変更と締め日の設定(client):
    res = client.post("/api/customers", json=customer_body(), headers=H)
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["terms"] is None  # 空なら会社の設定に従う

    res = client.put(f"/api/customers/{created['id']}",
                     json=customer_body(name="マスタテスト商会(新)", address="サンプル県みなと市1-1", terms=TERMS_20_10), headers=H)
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["name"] == "マスタテスト商会(新)"
    assert updated["terms"] == TERMS_20_10

    # 取引先の締め日で、締め請求の対象期間と支払期限が決まる
    billable = client.get("/api/invoices/billable-lines",
                          params={"customer_id": created["id"], "closing_date": "2026-11-20"}, headers=H).json()
    assert (billable["period_start"], billable["due_date"]) == ("2026-10-21", "2026-12-10")

    # 会社の設定に戻す
    res = client.put(f"/api/customers/{created['id']}", json=customer_body(terms=None), headers=H)
    assert res.json()["terms"] is None
    billable = client.get("/api/invoices/billable-lines",
                          params={"customer_id": created["id"], "closing_date": "2026-11-30"}, headers=H).json()
    assert billable["period_start"] == "2026-11-01"  # 会社の設定は月末締め


def test_取引先を変えても発行済みの書類の宛名は変わらない(client):
    customer = client.post("/api/customers", json=customer_body(name="旧社名株式会社"), headers=H).json()
    quote = client.post("/api/quotes", json={"customer_id": customer["id"], "title": None, "remarks": None,
                                             "lines": [{"description": "作業", "quantity": "1", "unit_price": "1000"}]},
                        headers=H).json()
    assert client.post(f"/api/quotes/{quote['id']}/issue", headers=H).status_code == 200
    client.put(f"/api/customers/{customer['id']}", json=customer_body(name="新社名株式会社"), headers=H)
    assert client.get(f"/api/quotes/{quote['id']}", headers=H).json()["customer_name"] == "旧社名株式会社"


def test_使用中止の取引先は一覧に出ない(client):
    customer = client.post("/api/customers", json=customer_body(name="使用中止テスト"), headers=H).json()
    client.put(f"/api/customers/{customer['id']}", json=customer_body(name="使用中止テスト", is_active=False), headers=H)
    names = [c["name"] for c in client.get("/api/customers", headers=H).json()]
    assert "使用中止テスト" not in names
    everyone = client.get("/api/customers", params={"include_inactive": "true"}, headers=H).json()
    assert [c["is_active"] for c in everyone if c["name"] == "使用中止テスト"] == [False]


def test_取引先の入力チェック(client):
    bad_terms = {"closing_day": 32, "payment_month_offset": 1, "payment_day": 10}
    assert client.post("/api/customers", json=customer_body(terms=bad_terms), headers=H).status_code == 422
    assert client.post("/api/customers", json=customer_body(honorific="殿"), headers=H).status_code == 422
    # 締め日・支払月・支払日は3つそろえて設定する(DB でも確認)
    conn = client.db_conn
    customer = client.post("/api/customers", json=customer_body(), headers=H).json()
    try:
        with conn.transaction():
            conn.execute("UPDATE app.customers SET closing_day = 20 WHERE id = %s", (customer["id"],))
        raise AssertionError("締め日だけの設定が通ってしまった")
    except Exception as e:  # noqa: BLE001
        assert "customers_terms_all_or_none" in str(e)


def test_ほかの会社の取引先は変更できない(client):
    customer = client.post("/api/customers", json=customer_body(), headers=H).json()
    res = client.put(f"/api/customers/{customer['id']}", json=customer_body(name="乗っ取り"), headers=HB)
    assert res.status_code == 404
    assert client.get(f"/api/customers/{customer['id']}", headers=H).json()["name"] == "マスタテスト商会"


# ---------------------------------------------------------------------
# 商品・サービス
# ---------------------------------------------------------------------
def item_body(**kw):
    return {"code": "WEB-01", "name": "ホームページ保守", "unit": "月", "unit_price": "30000",
            "tax_category": "standard", "is_active": True, **kw}


def test_商品の登録と変更(client):
    res = client.post("/api/items", json=item_body(code="ＷＥＢ－０１"), headers=H)
    assert res.status_code == 201, res.text
    item = res.json()
    assert item["code"] == "WEB-01"  # 全角は半角にそろえる(全角ハイフンは "-" に)
    assert item["unit_price"] == "30000.00"

    res = client.put(f"/api/items/{item['id']}", json=item_body(unit_price="35000", tax_category="reduced"), headers=H)
    assert res.status_code == 200
    assert (res.json()["unit_price"], res.json()["tax_category"]) == ("35000.00", "reduced")

    # 使用中止にすると候補に出ない
    client.put(f"/api/items/{item['id']}", json=item_body(is_active=False), headers=H)
    assert item["id"] not in [i["id"] for i in client.get("/api/items", headers=H).json()]
    assert item["id"] in [i["id"] for i in client.get("/api/items", params={"include_inactive": "true"}, headers=H).json()]


def test_商品コードの重複はエラー(client):
    assert client.post("/api/items", json=item_body(code="DUP-1"), headers=H).status_code == 201
    res = client.post("/api/items", json=item_body(code="DUP-1", name="別の商品"), headers=H)
    assert res.status_code == 409
    assert "DUP-1" in res.json()["detail"]
    # コードなしは何件でも登録できる
    assert client.post("/api/items", json=item_body(code=None, name="コードなし1"), headers=H).status_code == 201
    assert client.post("/api/items", json=item_body(code="  ", name="コードなし2"), headers=H).status_code == 201
    # 別の会社なら同じコードを使える
    assert client.post("/api/items", json=item_body(code="DUP-1"), headers=HB).status_code == 201


def test_商品は会社ごとに分かれる(client):
    client.post("/api/items", json=item_body(code="ONLY-A", name="A社だけの商品"), headers=H)
    assert "A社だけの商品" not in [i["name"] for i in client.get("/api/items", headers=HB).json()]


def test_商品の変更は履歴に残る(client):
    item = client.post("/api/items", json=item_body(code="LOG-1"), headers=H).json()
    client.put(f"/api/items/{item['id']}", json=item_body(code="LOG-1", unit_price="1"), headers=H)
    actions = client.db_conn.execute(
        "SELECT action FROM app.audit_logs WHERE table_name = 'items' AND record_id = %s ORDER BY id", (item["id"],)
    ).fetchall()
    assert [a["action"] for a in actions] == ["insert", "update"]


# ---------------------------------------------------------------------
# データの一括エクスポート
# ---------------------------------------------------------------------
def read_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    data = zf.read(name)
    assert data.startswith(b"\xef\xbb\xbf")  # Excel で文字化けしないよう BOM を付ける
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_エクスポートにすべてのデータと控えPDFが入る(client):
    customer = client.post("/api/customers", json=customer_body(name="エクスポート確認商店"), headers=H).json()
    client.post("/api/items", json=item_body(code="EXP-1", name="エクスポート確認の商品"), headers=H)
    quote = client.post("/api/quotes", json={"customer_id": customer["id"], "title": "書き出し確認", "remarks": None,
                                             "lines": [{"description": "作業", "quantity": "1", "unit_price": "5000"}]},
                        headers=H).json()
    issued = client.post(f"/api/quotes/{quote['id']}/issue", headers=H).json()
    client.post("/api/customers", json=customer_body(name="B社の取引先"), headers=HB)

    res = client.get("/api/export", headers=H)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "attachment" in res.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = zf.namelist()
    assert "はじめにお読みください.txt" in names and "manifest.csv" in names

    customers = read_csv(zf, "csv/customers.csv")
    assert "エクスポート確認商店" in [c["name"] for c in customers]
    assert {c["tenant_id"] for c in customers} == {TENANT_A}  # ほかの会社のデータは入らない
    assert "エクスポート確認の商品" in [i["name"] for i in read_csv(zf, "csv/items.csv")]
    assert [t["id"] for t in read_csv(zf, "csv/tenants.csv")] == [TENANT_A]
    assert any(q["quote_number"] == issued["quote_number"] for q in read_csv(zf, "csv/quotes.csv"))
    assert read_csv(zf, "csv/audit_logs.csv")  # 変更の履歴も入る

    # 控えPDFは発行時のハッシュ値と一致する
    pdf_name = f"控えPDF/見積書/{issued['quote_number']}_v1.pdf"
    assert pdf_name in names
    manifest = {m["ファイル"]: m for m in read_csv(zf, "manifest.csv")}
    entry = manifest[pdf_name]
    assert entry["SHA-256"] == entry["発行時のSHA-256"] == hashlib.sha256(zf.read(pdf_name)).hexdigest()
    assert entry["注意"] == ""
    assert manifest["csv/customers.csv"]["件数"] == str(len(customers))


def test_エクスポートは管理者だけ(client):
    assert client.get("/api/export", headers=HB).status_code == 403
