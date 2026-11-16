"""見積 API のテスト。作成 → 更新 → 発行 → 失注 の流れと、発行後の保護を確かめる。

変更はテストの最後に取り消される(conftest.py)。
"""

import hashlib
import re

import pytest

TENANT_A = "a1000000-0000-0000-0000-000000000001"  # サンプル商事(端数処理: 切り捨て)
TENANT_B = "b2000000-0000-0000-0000-000000000002"
H = {"X-Tenant-Id": TENANT_A}


@pytest.fixture(scope="module")
def customer_id(client):
    customers = client.get("/api/customers", headers=H).json()
    return next(c["id"] for c in customers if c["name"] == "株式会社瀬戸内物産")


def lines():
    return [
        {"description": "ホームページ制作", "quantity": "1", "unit": "式", "unit_price": "150000", "tax_category": "standard"},
        {"description": "保守作業", "quantity": "2.5", "unit": "時間", "unit_price": "3333", "tax_category": "standard"},
        {"description": "お茶菓子", "quantity": "3", "unit": "箱", "unit_price": "1234", "tax_category": "reduced"},
        {"description": "端数値引き", "quantity": "1", "unit": None, "unit_price": "-500", "tax_category": "standard"},
    ]


# 期待値(切り捨て)
#   明細: 150000 / 2.5×3333=8332.5→8332 / 3×1234=3702 / -500
#   10%対象: 150000+8332-500 = 157832 → 消費税 15783.2 → 15783
#   8%対象 : 3702 → 消費税 296.16 → 296
EXPECTED = {"subtotal": "161534", "tax": "16079", "total": "177613"}


def test_計算APIは保存せずに金額を返す(client):
    res = client.post("/api/calculate", json={"lines": lines()}, headers=H)
    assert res.status_code == 200
    body = res.json()

    assert [line["amount"] for line in body["lines"]] == ["150000", "8332", "3702", "-500"]
    assert [(s["tax_category"], s["taxable_amount"], s["tax_amount"]) for s in body["tax_summaries"]] == [
        ("standard", "157832", "15783"),
        ("reduced", "3702", "296"),
    ]
    assert (body["subtotal_amount"], body["tax_amount"], body["total_amount"]) == tuple(EXPECTED.values())


def test_作成から発行までの流れ(client, customer_id):
    # 作成(作成中)
    res = client.post("/api/quotes", json={"customer_id": customer_id, "title": "Web制作一式", "lines": lines()[:1]}, headers=H)
    assert res.status_code == 201
    quote = res.json()
    assert quote["status"] == "draft"
    assert quote["quote_number"] is None
    assert quote["customer_name"] == "株式会社瀬戸内物産"

    # 更新(明細の入れ替え)。保存された金額は計算APIと一致する
    res = client.put(f"/api/quotes/{quote['id']}", json={"customer_id": customer_id, "title": "Web制作一式", "lines": lines()}, headers=H)
    assert res.status_code == 200
    quote = res.json()
    assert len(quote["lines"]) == 4
    assert (quote["subtotal_amount"], quote["tax_amount"], quote["total_amount"]) == tuple(EXPECTED.values())

    # 作成中は下書きのプレビューPDFが出る
    res = client.get(f"/api/quotes/{quote['id']}/pdf", headers=H)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")

    # 発行: 番号・発行日・有効期限が入る
    res = client.post(f"/api/quotes/{quote['id']}/issue", headers=H)
    assert res.status_code == 200
    issued = res.json()
    assert issued["status"] == "issued"
    assert re.fullmatch(r"Q-\d{4}-\d{4}", issued["quote_number"])
    assert issued["issue_date"] is not None
    assert issued["valid_until"] > issued["issue_date"]
    assert issued["total_amount"] == EXPECTED["total"]

    # 発行後のPDFは保存した控えそのもの(何度取っても同じ内容)
    first = client.get(f"/api/quotes/{quote['id']}/pdf", headers=H).content
    second = client.get(f"/api/quotes/{quote['id']}/pdf", headers=H).content
    assert first == second
    assert first.startswith(b"%PDF")

    # 発行控えの記録(検索用の項目とハッシュ)
    docs = client.get("/api/quotes", headers=H).json()
    assert any(d["id"] == quote["id"] and d["status"] == "issued" for d in docs)
    assert hashlib.sha256(first).hexdigest()  # 控えのハッシュ計算ができること

    # 発行後は変更・削除・再発行できない
    put = client.put(f"/api/quotes/{quote['id']}", json={"customer_id": customer_id, "lines": lines()}, headers=H)
    assert put.status_code == 409
    assert client.delete(f"/api/quotes/{quote['id']}", headers=H).status_code == 409
    assert client.post(f"/api/quotes/{quote['id']}/issue", headers=H).status_code == 409

    # 失注にできる
    lost = client.post(f"/api/quotes/{quote['id']}/lose", headers=H)
    assert lost.status_code == 200
    assert lost.json()["status"] == "lost"
    assert client.post(f"/api/quotes/{quote['id']}/lose", headers=H).status_code == 409

    # 一覧の絞り込み
    closed = client.get("/api/quotes", params={"filter": "closed"}, headers=H).json()
    assert any(d["id"] == quote["id"] for d in closed)
    drafts = client.get("/api/quotes", params={"filter": "draft"}, headers=H).json()
    assert all(d["id"] != quote["id"] for d in drafts)


def test_作成中の見積は削除できる(client, customer_id):
    quote = client.post("/api/quotes", json={"customer_id": customer_id, "lines": lines()[:1]}, headers=H).json()
    assert client.delete(f"/api/quotes/{quote['id']}", headers=H).status_code == 204
    assert client.get(f"/api/quotes/{quote['id']}", headers=H).status_code == 404


def test_発行すると番号が連番になる(client, customer_id):
    numbers = []
    for _ in range(2):
        quote = client.post("/api/quotes", json={"customer_id": customer_id, "lines": lines()[:1]}, headers=H).json()
        numbers.append(client.post(f"/api/quotes/{quote['id']}/issue", headers=H).json()["quote_number"])
    assert int(numbers[1][-4:]) == int(numbers[0][-4:]) + 1


def test_他社の見積は見えない(client, customer_id):
    quote = client.post("/api/quotes", json={"customer_id": customer_id, "lines": lines()[:1]}, headers=H).json()
    other = {"X-Tenant-Id": TENANT_B}
    assert client.get(f"/api/quotes/{quote['id']}", headers=other).status_code == 404
    assert all(d["id"] != quote["id"] for d in client.get("/api/quotes", headers=other).json())


def test_他社の取引先では作れない(client):
    b_customers = client.get("/api/customers", headers={"X-Tenant-Id": TENANT_B}).json()
    res = client.post("/api/quotes", json={"customer_id": b_customers[0]["id"], "lines": lines()[:1]}, headers=H)
    assert res.status_code == 422


@pytest.mark.parametrize(
    "bad_line",
    [
        {"description": "", "quantity": "1", "unit_price": "100"},          # 品名なし
        {"description": "数量ゼロ", "quantity": "0", "unit_price": "100"},  # 数量は0より大きい
        {"description": "税区分不正", "quantity": "1", "unit_price": "100", "tax_category": "ten"},
    ],
)
def test_不正な明細は422(client, customer_id, bad_line):
    res = client.post("/api/quotes", json={"customer_id": customer_id, "lines": [bad_line]}, headers=H)
    assert res.status_code == 422


def test_全角の英数字は保存時に半角になる(client, customer_id):
    body = {
        "customer_id": customer_id,
        "title": "システム運用（２０２６年度）",
        "remarks": "請求は各月ごと。担当：ＩＴ課",
        "lines": [{"description": "２０２６年１２月分", "quantity": "1", "unit": "ｋｇ", "unit_price": "50000"}],
    }
    quote = client.post("/api/quotes", json=body, headers=H).json()
    assert quote["title"] == "システム運用（2026年度）"
    assert quote["remarks"] == "請求は各月ごと。担当：IT課"
    assert quote["lines"][0]["description"] == "2026年12月分"
    assert quote["lines"][0]["unit"] == "kg"
