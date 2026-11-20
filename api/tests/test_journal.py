"""会計ソフト向けの仕訳CSVのテスト。変更はテストの最後に取り消される(conftest.py)。"""

import csv
import io
from decimal import Decimal

import pytest

from app.domain.journal import DEFAULT_TAX_LABELS, SettingsProblem, default_accounts, render, reverse_lines, side

TENANT_A = "a1000000-0000-0000-0000-000000000001"
TENANT_B = "b2000000-0000-0000-0000-000000000002"
H = {"X-Tenant-Id": TENANT_A}
HB = {"X-Tenant-Id": TENANT_B}
PERIOD = {"date_from": "2026-10-01", "date_to": "2026-12-31"}


# ---------------------------------------------------------------------
# 会計ソフトごとの CSV(DB を使わない)
# ---------------------------------------------------------------------
def settings_for(software, **over):
    return {
        "software": software, "sales_date_basis": "transaction", "customer_as_sub": over.get("customer_as_sub", True),
        "accounts": over.get("accounts", default_accounts(False)),
        "tax_labels": {sw: dict(v) for sw, v in DEFAULT_TAX_LABELS.items()} | over.get("tax_labels", {}),
    }


SALE = {
    "date": "2026-11-20", "summary": "みなと物産 請求書INV-2026-0001", "partner": "株式会社みなと物産",
    "lines": [
        {"debit": side("receivable", Decimal(110000)), "credit": side("sales", Decimal(110000), "sales_standard", Decimal(10000))},
        {"debit": side("receivable", Decimal(10800)), "credit": side("sales", Decimal(10800), "sales_reduced", Decimal(800))},
    ],
}


def rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("cp932"))))


def test_弥生インポート形式():
    data, filename, replaced = render("yayoi", [SALE], settings_for("yayoi"))
    assert filename.endswith(".txt") and replaced == 0
    r = rows(data)
    assert len(r) == 2 and all(len(row) == 25 for row in r)  # 見出し行なし・25項目
    assert [row[0] for row in r] == ["2110", "2101"]  # 複数行の伝票の1行目・最終行
    assert r[0][3:10] == ["2026/11/20", "売掛金", "株式会社みなと物産", "", "対象外", "110000", ""]
    assert r[0][10:16] == ["売上高", "", "", "課税売上込10%", "110000", "10000"]
    assert r[1][13:16] == ["課税売上込軽減8%", "10800", "800"]
    assert (r[0][19], r[0][24]) == ("3", "no")  # 振替伝票・調整なし


def test_マネーフォワード形式():
    data, _, _ = render("mf", [SALE], settings_for("mf", customer_as_sub=False))
    r = rows(data)
    assert r[0][:3] == ["取引No", "取引日", "借方勘定科目"] and len(r[0]) == 27
    assert [row[0] for row in r[1:]] == ["1", "1"]  # 同じ取引Noで1つの複合仕訳
    assert r[1][2:10] == ["売掛金", "", "", "株式会社みなと物産", "対象外", "", "110000", ""]
    assert r[1][10:18] == ["売上高", "", "", "", "課税売上 10%", "", "110000", ""]  # 税額は空欄(MFの指定)
    assert r[2][14] == "課税売上 (軽)8%"


def test_freee形式():
    data, _, _ = render("freee", [SALE, SALE], settings_for("freee"))
    r = rows(data)
    assert r[0][:4] == ["[表題行]", "日付", "伝票番号", "決算整理仕訳"]
    assert [row[0] for row in r[1:]] == ["[明細行]"] * 4
    assert [row[2] for row in r[1:]] == ["1", "1", "2", "2"]  # 伝票番号で仕訳をまとめる
    header = r[0]
    first = dict(zip(header, r[1]))
    assert (first["借方勘定科目"], first["借方補助科目"], first["借方取引先"], first["借方税区分"]) == (
        "売掛金", "株式会社みなと物産", "株式会社みなと物産", "対象外")
    assert (first["貸方税区分"], first["貸方金額"], first["貸方税額"]) == ("課税売上10%", "110000", "10000")


def test_勘定奉行はコードが必要():
    with pytest.raises(SettingsProblem, match="コード"):
        render("kanjo_bugyo", [SALE], settings_for("kanjo_bugyo"))
    accounts = default_accounts(False)
    for key, code in zip(accounts, ["135", "500", "111", "741", "199"]):
        accounts[key]["code"] = code
    labels = {"kanjo_bugyo": {"sales_standard": "11", "sales_reduced": "11", "sales_exempt": "12", "sales_non_taxable": "0",
                              "sales_export": "13", "out_of_scope": "0", "purchase_standard": "21"}}
    data, filename, _ = render("kanjo_bugyo", [SALE], settings_for("kanjo_bugyo", accounts=accounts, tax_labels=labels))
    r = rows(data)
    assert r[0][:2] == ["OBCD001", "CSJS005"] and len(r[0]) == 21
    assert [row[0] for row in r[1:]] == ["*", ""]  # 伝票の1行目に「*」
    head = dict(zip(r[0], r[1]))
    assert (head["CSJS201"], head["CSJS203"], head["CSJS213"]) == ("135", "0", "110000")
    assert (head["CSJS301"], head["CSJS303"], head["CSJS322"], head["CSJS320"]) == ("500", "11", "0", "10")
    assert dict(zip(r[0], r[2]))["CSJS322"] == "1"  # 軽減税率


def test_源泉徴収の勘定の初期値():
    assert default_accounts(False)["withholding"]["name"] == "仮払税金"
    assert default_accounts(True)["withholding"]["name"] == "事業主貸"  # 個人事業主


def test_逆仕訳は貸借を入れ替える():
    rev = reverse_lines(SALE["lines"])
    assert rev[0]["debit"]["account"] == "sales" and rev[0]["credit"]["account"] == "receivable"


def test_ShiftJISにない文字は置き換えて数える():
    entry = {**SALE, "summary": "請求書 🍣 寿司代"}
    data, _, replaced = render("yayoi", [entry], settings_for("yayoi"))
    assert replaced == 2 and "請求書 ? 寿司代" in data.decode("cp932")  # 2行の摘要にそれぞれ1文字


# ---------------------------------------------------------------------
# 出力の流れ
# ---------------------------------------------------------------------
def issued(client, customer_id, lines, **extra):
    draft = client.post("/api/invoices", json={"customer_id": customer_id, "lines": lines, **extra}, headers=H)
    assert draft.status_code == 201, draft.text
    res = client.post(f"/api/invoices/{draft.json()['id']}/issue", headers=H)
    assert res.status_code == 200, res.text
    return res.json()


def line(price, tax="standard", day="2026-10-20"):
    return {"description": "作業", "quantity": "1", "unit_price": price, "tax_category": tax, "transaction_date": day}


@pytest.fixture(scope="module")
def data(client):
    customer = client.post("/api/customers", json={"name": "仕訳テスト株式会社"}, headers=H).json()["id"]
    inv = issued(client, customer, [line("100000"), line("10000", "reduced")])  # 110,000 + 10,800 円
    receipt = client.post("/api/receipts", json={"customer_id": customer, "received_on": "2026-11-10", "amount": "50000"},
                          headers=H).json()
    payment = client.post(f"/api/invoices/{inv['id']}/payments", json={"paid_on": "2026-11-12", "amount": "1000"}, headers=H)
    assert payment.status_code == 201, payment.text
    return {"customer": customer, "invoice": inv, "receipt": receipt}


def pending(client, **period):
    res = client.get("/api/journal/pending", params={**PERIOD, **period}, headers=H)
    assert res.status_code == 200, res.text
    return res.json()


def test_設定の初期値(client):
    s = client.get("/api/journal/settings", headers=H).json()
    assert (s["software"], s["sales_date_basis"], s["can_edit"]) == ("yayoi", "transaction", True)
    assert s["accounts"]["receivable"]["name"] == "売掛金"
    assert s["accounts"]["withholding"]["name"] == "仮払税金"  # 法人
    assert s["tax_labels"]["mf"]["sales_standard"] == "課税売上 10%"


def test_売上と入金の仕訳(client, data):
    body = pending(client)
    mine = [e for e in body["entries"] if "仕訳テスト" in e["summary"]]
    sale = next(e for e in mine if e["source_type"] == "invoice")
    assert sale["date"] == "2026-10-20"  # 取引日基準(納品日)
    assert [(l["debit"]["account"], l["credit"]["account"], l["credit"]["tax"], l["credit"]["amount"], l["credit"]["tax_amount"])
            for l in sale["lines"]] == [
        ("売掛金", "売上高", "課税売上込10%", "110000", "10000"),
        ("売掛金", "売上高", "課税売上込軽減8%", "10800", "800"),
    ]
    assert sale["lines"][0]["debit"]["sub"] == "仕訳テスト株式会社"  # 売掛金の補助科目に取引先
    receipt = next(e for e in mine if e["source_type"] == "receipt")
    assert [(l["debit"]["account"], l["credit"]["account"], l["debit"]["amount"]) for l in receipt["lines"]] == [
        ("普通預金", "売掛金", "50000")]
    payment = next(e for e in mine if e["source_type"] == "payment")
    assert payment["lines"][0]["debit"]["amount"] == "1000"
    assert body["problem"] is None


def test_計上日を請求日にできる(client, data):
    s = client.get("/api/journal/settings", headers=H).json()
    body = {k: s[k] for k in ("software", "customer_as_sub", "accounts", "tax_labels")} | {"sales_date_basis": "issue"}
    assert client.put("/api/journal/settings", json=body, headers=H).status_code == 200
    sale = next(e for e in pending(client)["entries"] if e["source_type"] == "invoice" and "仕訳テスト" in e["summary"])
    assert sale["date"] == data["invoice"]["issue_date"]
    body["sales_date_basis"] = "transaction"
    client.put("/api/journal/settings", json=body, headers=H)


def test_期間より前の未出力を知らせる(client, data):
    body = pending(client, date_from="2026-11-01", date_to="2026-11-30")
    assert not any(e["source_type"] == "invoice" and "仕訳テスト" in e["summary"] for e in body["entries"])
    assert body["before_count"] >= 1


def test_出力すると二重に出さない(client, data):
    res = client.post("/api/journal/exports", json=PERIOD, headers=H)
    assert res.status_code == 201, res.text
    export = res.json()
    assert export["software_name"] == "弥生会計" and export["entry_count"] >= 3 and export["is_latest"]

    file = client.get(f"/api/journal/exports/{export['id']}/file", headers=H)
    assert file.status_code == 200
    assert "attachment" in file.headers["content-disposition"]
    text = file.content.decode("cp932")
    assert "仕訳テスト株式会社 請求書" in text

    assert pending(client)["entries"] == []
    assert client.post("/api/journal/exports", json=PERIOD, headers=H).status_code == 409


def test_出力後の訂正と入金の取消は逆仕訳になる(client, data):
    inv = data["invoice"]
    draft = client.post(f"/api/invoices/{inv['id']}/corrections", headers=H).json()
    client.put(f"/api/invoices/{draft['id']}", json={"customer_id": data["customer"], "lines": [line("90000")]}, headers=H)
    v2 = client.post(f"/api/invoices/{draft['id']}/issue", headers=H).json()
    assert client.delete(f"/api/receipts/{data['receipt']['id']}", headers=H).status_code == 204

    entries = [e for e in pending(client)["entries"] if "仕訳テスト" in e["summary"]]
    kinds = sorted((e["kind"], e["source_type"]) for e in entries)
    assert kinds == [("entry", "invoice"), ("reversal", "invoice"), ("reversal", "receipt")]
    rev = next(e for e in entries if e["kind"] == "reversal" and e["source_type"] == "invoice")
    assert rev["summary"].startswith("【訂正】")
    assert (rev["lines"][0]["debit"]["account"], rev["lines"][0]["credit"]["account"]) == ("売上高", "売掛金")
    new = next(e for e in entries if e["kind"] == "entry")
    assert new["lines"][0]["credit"]["amount"] == "99000" and "(第2版)" in new["summary"]
    assert v2["version"] == 2


def test_最後の出力だけ取り消せる(client, data):
    first = client.get("/api/journal/exports", headers=H).json()[0]
    second = client.post("/api/journal/exports", json=PERIOD, headers=H).json()
    assert client.delete(f"/api/journal/exports/{first['id']}", headers=H).status_code == 409
    assert client.delete(f"/api/journal/exports/{second['id']}", headers=H).status_code == 204
    # 取り消した分は、また出力の対象に戻る
    assert any(e["kind"] == "reversal" for e in pending(client)["entries"])


def test_勘定奉行はコードを設定するまで出力できない(client, data):
    s = client.get("/api/journal/settings", headers=H).json()
    body = {k: s[k] for k in ("sales_date_basis", "customer_as_sub", "accounts", "tax_labels")} | {"software": "kanjo_bugyo"}
    assert client.put("/api/journal/settings", json=body, headers=H).status_code == 200
    assert "コード" in pending(client)["problem"]
    assert client.post("/api/journal/exports", json=PERIOD, headers=H).status_code == 422
    body["software"] = "yayoi"
    client.put("/api/journal/settings", json=body, headers=H)


def test_設定は管理者だけが変えられる(client):
    s = client.get("/api/journal/settings", headers=HB).json()
    assert s["can_edit"] is False
    body = {k: s[k] for k in ("software", "sales_date_basis", "customer_as_sub", "accounts", "tax_labels")}
    assert client.put("/api/journal/settings", json=body, headers=HB).status_code == 403


def test_ほかの会社の仕訳は出ない(client, data):
    body = client.get("/api/journal/pending", params=PERIOD, headers=HB).json()
    assert not any("仕訳テスト" in e["summary"] for e in body["entries"])
    assert client.get("/api/journal/exports", headers=HB).json() == []


def test_振込手数料と源泉徴収の仕訳(client):
    """入金消込で振込手数料を差し引いた入金と、個人事業主の源泉徴収。"""
    current = client.get("/api/company", headers=H).json()
    profile = {**current["profile"], "business_type": "sole_proprietor", "representative_name": "山田 太郎"}
    assert client.put("/api/company", json={"profile": profile, "terms": current["terms"]}, headers=H).status_code == 200
    customer = client.post("/api/customers", json={"name": "源泉仕訳テスト"}, headers=H).json()["id"]
    inv = issued(client, customer, [line("100000", day="2026-11-05")], withholding=True)  # 源泉 10,210円
    assert inv["withholding_amount"] == "10210"

    import base64
    fmt = client.post("/api/bank/formats", json={"name": "仕訳テスト銀行", "date_col": 0, "amount_col": 1, "payer_col": 2},
                      headers=H).json()
    net = 110000 - 10210 - 660
    csv_text = f"日付,入金,名義\n2026/11/25,{net},ｹﾞﾝｾﾝﾃｽﾄ\n"
    client.post("/api/bank/imports", json={"format_id": fmt["id"], "content_base64": base64.b64encode(csv_text.encode()).decode()},
                headers=H)
    st = next(s for s in client.get("/api/bank/statements", headers=H).json()["statements"] if s["payer_name"] == "ｹﾞﾝｾﾝﾃｽﾄ")
    res = client.post(f"/api/bank/statements/{st['id']}/match",
                      json={"customer_id": customer, "invoice_ids": [inv["id"]], "fee_amount": "660"}, headers=H)
    assert res.status_code == 200, res.text

    entries = [e for e in pending(client, date_from="2026-11-01", date_to="2026-11-30")["entries"] if "源泉仕訳" in e["summary"]]
    sale = next(e for e in entries if e["source_type"] == "invoice")
    # 勘定科目は仕訳の設定に従う(このファイルの前のテストで法人のときの「仮払税金」を保存している)
    assert (sale["lines"][-1]["debit"]["account"], sale["lines"][-1]["debit"]["amount"]) == ("仮払税金", "10210")
    assert (sale["lines"][-1]["credit"]["account"], sale["lines"][-1]["credit"]["sub"]) == ("売掛金", "源泉仕訳テスト")
    receipt = next(e for e in entries if e["source_type"] == "receipt")
    assert [(l["debit"]["account"], l["debit"]["tax"], l["debit"]["amount"]) for l in receipt["lines"]] == [
        ("普通預金", "対象外", str(net)), ("支払手数料", "課対仕入込10%", "660")]
    client.put("/api/company", json={"profile": current["profile"], "terms": current["terms"]}, headers=H)
