"""入金消込のテスト。変更はテストの最後に取り消される(conftest.py)。"""

import base64
from datetime import date
from decimal import Decimal

import pytest

from app.domain.bank import decode, normalize_payer, parse_amount, parse_date, payer_similarity, read_rows
from app.routers.bank import guess_columns

TENANT_A = "a1000000-0000-0000-0000-000000000001"
TENANT_B = "b2000000-0000-0000-0000-000000000002"
H = {"X-Tenant-Id": TENANT_A}
HB = {"X-Tenant-Id": TENANT_B}


# ---------------------------------------------------------------------
# 読み取りと名義の正規化(DB を使わない)
# ---------------------------------------------------------------------
@pytest.mark.parametrize("raw, key", [
    ("ｶ)ｾﾄｳﾁﾌﾞﾂｻﾝ", "セトウチブツサン"),            # 半角カナ・法人の略語(前)
    ("セトウチブッサン(カ", "セトウチブツサン"),        # 略語(後ろ)・小さい「ッ」
    ("ﾌｸﾔﾏｾｲｷ(ｶ)", "フクヤマセイキ"),
    ("ユ)ABCｼｮｳｼﾞ", "ABCシヨウジ"),                  # 有限会社・英字
    ("1234567890 ｶ)ｻﾝﾌﾟﾙ", "サンプル"),               # 振込依頼人コード付き
    ("みやじま かんこう", "ミヤジマカンコウ"),         # ひらがな・空白
])
def test_振込名義をそろえる(raw, key):
    assert normalize_payer(raw) == key


def test_名義の近さ():
    assert payer_similarity("セトウチブツサン", "セトウチブツサン") == 2
    assert payer_similarity("セトウチブツサンオカヤマシテン", "セトウチブツサン") == 1
    assert payer_similarity("ABC", "ABCD") == 0  # 短い名義は「含む」で照合しない


@pytest.mark.parametrize("raw, expected", [
    ("2026/11/10", date(2026, 11, 10)),
    ("2026-1-5", date(2026, 1, 5)),
    ("20261110", date(2026, 11, 10)),
    ("2026年11月10日", date(2026, 11, 10)),
    ("令和8年11月10日", date(2026, 11, 10)),
    ("R08.11.10", date(2026, 11, 10)),
    ("2026/11/10 10:15", date(2026, 11, 10)),
    ("2026/02/30", None),
    ("入金", None),
])
def test_日付を読む(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("1,234", Decimal(1234)), ("¥1,234", Decimal(1234)), ("１，２３４円", Decimal(1234)),
    ("△500", Decimal(-500)), ("", None), ("-", None),
])
def test_金額を読む(raw, expected):
    assert parse_amount(raw) == expected


def test_文字コードを判定する():
    text = "取引日,お預入金額\n2026/11/10,1000\n"
    assert decode(text.encode("cp932")) == (text, "shift_jis")
    assert decode(("﻿" + text).encode("utf-8")) == (text, "utf-8")


def test_見出しから列を推測する():
    rows = read_rows("口座:普通 1234567\n取引日,摘要,お支払金額,お預り金額,差引残高\n2026/11/10,ｶ)ﾃｽﾄ,,1000,5000\n")
    assert guess_columns(rows) == {"skip_rows": 2, "date_col": 0, "amount_col": 3, "payer_col": 1, "memo_col": None}


# ---------------------------------------------------------------------
# 取込から消込まで
# ---------------------------------------------------------------------
def issued_invoice(client, customer_id, unit_price, day):
    order = client.post("/api/orders", json={"customer_id": customer_id, "order_date": day, "lines": [
        {"description": "作業", "quantity": "1", "unit_price": unit_price, "delivered_on": day}]}, headers=H).json()
    draft = client.post(f"/api/invoices/from-order/{order['id']}", headers=H).json()
    issued = client.post(f"/api/invoices/{draft['id']}/issue", headers=H)
    assert issued.status_code == 200, issued.text
    return issued.json()


def csv_b64(text: str) -> str:
    return base64.b64encode(text.encode("cp932")).decode()


BANK_CSV = """取引日,摘要,お支払金額,お預り金額,差引残高
2026/11/10,ｶ)ｹｼｺﾐﾃｽﾄ,,109340,1109340
2026/11/10,ﾃﾞﾝｷﾀﾞｲ,12000,,1097340
2026/11/12,ﾍﾞﾂﾒｲｷﾞ ｼｮｳｼﾞ,,55000,1152340
2026/11/15,ﾘｿｸ,,12,1152352
2026/11/16,ｶ)ｹｼｺﾐﾃｽﾄ,,30000,1182352
"""


@pytest.fixture(scope="module")
def setup(client):
    """取引先2社(1社は名義を登録済み)と、発行済みの請求書3枚。"""
    c1 = client.post("/api/customers", json={"name": "消込テスト株式会社", "payer_names": ["ｶ)ｹｼｺﾐﾃｽﾄ"]}, headers=H).json()
    c2 = client.post("/api/customers", json={"name": "別名義商事"}, headers=H).json()
    inv1 = issued_invoice(client, c1["id"], "100000", "2026-10-20")   # 110,000円
    inv2 = issued_invoice(client, c2["id"], "50000", "2026-10-25")    # 55,000円
    inv3 = issued_invoice(client, c1["id"], "40000", "2026-11-05")    # 44,000円
    fmt = client.post("/api/bank/formats", json={"name": "テスト銀行 普通", "skip_rows": 1, "date_col": 0,
                                                 "amount_col": 3, "payer_col": 1}, headers=H)
    assert fmt.status_code == 201, fmt.text
    return {"c1": c1, "c2": c2, "inv1": inv1, "inv2": inv2, "inv3": inv3, "format": fmt.json()}


def open_statements(client):
    return {s["payer_name"] + "/" + s["amount"]: s for s in
            client.get("/api/bank/statements", params={"status": "open"}, headers=H).json()["statements"]}


def test_プレビューで列を推測する(client, setup):
    res = client.post("/api/bank/preview", json={"content_base64": csv_b64(BANK_CSV)}, headers=H)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["encoding"] == "shift_jis"
    assert body["rows"][1][1] == "ｶ)ｹｼｺﾐﾃｽﾄ"
    assert body["guess"] == {"skip_rows": 1, "date_col": 0, "amount_col": 3, "payer_col": 1, "memo_col": None}


def test_入金の行だけを取り込み_二重に取り込まない(client, setup):
    body = {"format_id": setup["format"]["id"], "filename": "nov.csv", "content_base64": csv_b64(BANK_CSV)}
    res = client.post("/api/bank/imports", json=body, headers=H)
    assert res.status_code == 201, res.text
    assert (res.json()["imported"], res.json()["duplicates"], res.json()["skipped"]) == (4, 0, 1)  # 出金の1行は飛ばす

    again = client.post("/api/bank/imports", json=body, headers=H).json()
    assert (again["imported"], again["duplicates"]) == (0, 4)
    assert len(open_statements(client)) == 4


def test_候補_名義と金額_振込手数料の差額(client, setup):
    s = open_statements(client)["ｶ)ｹｼｺﾐﾃｽﾄ/109340"]
    best = s["candidates"][0]
    assert (best["customer_name"], best["basis"], best["match"], best["fee_amount"]) == ("消込テスト株式会社", "name_amount", "fee", "660")
    assert [i["invoice_number"] for i in best["invoices"]] == [setup["inv1"]["invoice_number"]]


def test_候補_金額だけ一致(client, setup):
    s = open_statements(client)["ﾍﾞﾂﾒｲｷﾞ ｼｮｳｼﾞ/55000"]
    best = s["candidates"][0]
    assert (best["customer_name"], best["basis"], best["match"]) == ("別名義商事", "amount", "exact")


def test_候補_名義だけ一致なら一部入金(client, setup):
    s = open_statements(client)["ｶ)ｹｼｺﾐﾃｽﾄ/30000"]
    best = s["candidates"][0]
    assert (best["basis"], best["match"]) == ("name", "partial")
    assert [i["invoice_number"] for i in best["invoices"]] == [setup["inv1"]["invoice_number"]]  # 古い請求書から


def test_確定の入力チェック(client, setup):
    s = open_statements(client)["ｶ)ｹｼｺﾐﾃｽﾄ/109340"]
    url = f"/api/bank/statements/{s['id']}/match"
    base = {"customer_id": setup["c1"]["id"], "invoice_ids": [setup["inv1"]["id"]]}
    assert client.post(url, json={**base, "fee_amount": "1000"}, headers=H).status_code == 422  # 上限(880円)超え
    assert client.post(url, json={**base, "fee_amount": "500"}, headers=H).status_code == 422   # 差額と違う
    res = client.post(url, json={"customer_id": setup["c1"]["id"], "invoice_ids": [setup["inv2"]["id"]]}, headers=H)
    assert res.status_code == 409  # 別の取引先の請求書


def test_振込手数料を差し引いた入金を消し込む(client, setup):
    s = open_statements(client)["ｶ)ｹｼｺﾐﾃｽﾄ/109340"]
    res = client.post(f"/api/bank/statements/{s['id']}/match", json={
        "customer_id": setup["c1"]["id"], "invoice_ids": [setup["inv1"]["id"]], "fee_amount": "660"}, headers=H)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "matched"
    assert (out["receipt"]["fee_amount"], out["receipt"]["invoice_numbers"]) == ("660", [setup["inv1"]["invoice_number"]])

    invoice = client.get(f"/api/invoices/{setup['inv1']['id']}", headers=H).json()
    assert (invoice["status"], invoice["balance"]) == ("paid", "0")
    assert sorted((p["kind"], p["amount"]) for p in invoice["payments"]) == [("bank_fee", "660"), ("deposit", "109340")]
    # 二重に確定できない
    again = client.post(f"/api/bank/statements/{s['id']}/match", json={
        "customer_id": setup["c1"]["id"], "invoice_ids": [setup["inv3"]["id"]]}, headers=H)
    assert again.status_code == 409


def test_確定すると振込名義を覚える(client, setup):
    s = open_statements(client)["ﾍﾞﾂﾒｲｷﾞ ｼｮｳｼﾞ/55000"]
    res = client.post(f"/api/bank/statements/{s['id']}/match", json={
        "customer_id": setup["c2"]["id"], "invoice_ids": [setup["inv2"]["id"]]}, headers=H)
    assert res.status_code == 200, res.text
    customer = client.get(f"/api/customers/{setup['c2']['id']}", headers=H).json()
    assert customer["payer_names"] == ["ﾍﾞﾂﾒｲｷﾞ ｼｮｳｼﾞ"]


def test_一部入金(client, setup):
    s = open_statements(client)["ｶ)ｹｼｺﾐﾃｽﾄ/30000"]
    res = client.post(f"/api/bank/statements/{s['id']}/match", json={
        "customer_id": setup["c1"]["id"], "invoice_ids": [setup["inv3"]["id"]]}, headers=H)
    assert res.status_code == 200, res.text
    invoice = client.get(f"/api/invoices/{setup['inv3']['id']}", headers=H).json()
    assert (invoice["status"], invoice["balance"]) == ("issued", "14000")


def test_対象外と戻す(client, setup):
    s = open_statements(client)["ﾘｿｸ/12"]
    res = client.post(f"/api/bank/statements/{s['id']}/ignore", json={"reason": "預金利息"}, headers=H)
    assert (res.json()["status"], res.json()["ignore_reason"]) == ("ignored", "預金利息")
    counts = client.get("/api/bank/statements", params={"status": "ignored"}, headers=H).json()["counts"]
    assert counts == {"open": 0, "matched": 3, "ignored": 1}
    assert client.delete(f"/api/bank/statements/{s['id']}/ignore", headers=H).json()["status"] == "open"


def test_確定を取り消すと請求書は未入金に戻る(client, setup):
    matched = client.get("/api/bank/statements", params={"status": "matched"}, headers=H).json()["statements"]
    s = next(m for m in matched if m["amount"] == "109340")
    res = client.delete(f"/api/bank/statements/{s['id']}/match", headers=H)
    assert res.status_code == 200 and res.json()["status"] == "open"
    invoice = client.get(f"/api/invoices/{setup['inv1']['id']}", headers=H).json()
    assert (invoice["status"], invoice["balance"]) == ("issued", "110000")


def test_入金の画面で取り消しても明細は未消込に戻る(client, setup):
    matched = client.get("/api/bank/statements", params={"status": "matched"}, headers=H).json()["statements"]
    s = next(m for m in matched if m["amount"] == "55000")
    assert client.delete(f"/api/receipts/{s['receipt']['id']}", headers=H).status_code == 204
    assert "ﾍﾞﾂﾒｲｷﾞ ｼｮｳｼﾞ/55000" in open_statements(client)


def test_確定した明細がある取り込みは取り消せない(client, setup):
    imports = client.get("/api/bank/imports", headers=H).json()
    first = next(i for i in imports if i["imported_count"] == 4)
    assert first["matched_count"] == 1
    assert client.delete(f"/api/bank/imports/{first['id']}", headers=H).status_code == 409
    # 確定のない取り込み(二重で0件だった方)は取り消せる
    empty = next(i for i in imports if i["imported_count"] == 0)
    assert client.delete(f"/api/bank/imports/{empty['id']}", headers=H).status_code == 204


def test_ほかの会社の明細は見えない(client, setup):
    body = client.get("/api/bank/statements", headers=HB).json()
    assert body["statements"] == [] and body["counts"] == {"open": 0, "matched": 0, "ignored": 0}
    assert client.get("/api/bank/formats", headers=HB).json() == []


def test_振込手数料の上限は自社情報で変えられる(client, setup):
    company = client.get("/api/company", headers=H).json()
    assert company["bank_fee_limit"] == 880
    res = client.put("/api/company", json={"profile": company["profile"], "terms": company["terms"], "bank_fee_limit": 440},
                     headers=H)
    assert res.json()["bank_fee_limit"] == 440
    s = open_statements(client)["ｶ)ｹｼｺﾐﾃｽﾄ/109340"]
    assert s["candidates"][0]["match"] != "fee"  # 660円の差額は上限を超えるので、手数料の候補にならない
    # 上限を省略して保存しても変わらない
    res = client.put("/api/company", json={"profile": company["profile"], "terms": company["terms"]}, headers=H)
    assert res.json()["bank_fee_limit"] == 440
