"""注文書の取り込み(AI で読み取り、人が確認して受注にする)の API。

AI は fake(見本の注文書にだけ決まった答えを返す)を使う。外部の API は呼ばない。
見本の注文書: scripts/make_sample_orders.py(docs/samples/orders と同じもの)
"""

import base64
import importlib.util
import tempfile
from pathlib import Path

import pytest

from app.config import settings

TENANT_A = "a1000000-0000-0000-0000-000000000001"  # サンプル商事
TENANT_B = "b2000000-0000-0000-0000-000000000002"
YAMADA = "22222222-0000-0000-0000-000000000001"   # 管理者
SUZUKI = "22222222-0000-0000-0000-000000000003"   # 一般

# 見本の注文書はテストのたびに作る(コンテナには docs フォルダがないため)
SAMPLES = Path(tempfile.mkdtemp(prefix="sme-orders-"))
_maker = importlib.util.spec_from_file_location("make_sample_orders", Path(__file__).resolve().parents[1] / "scripts" / "make_sample_orders.py")
_module = importlib.util.module_from_spec(_maker)
_maker.loader.exec_module(_module)
_module.main(SAMPLES)
SAMPLE_1 = SAMPLES / "注文書_見本1_みなと物産.pdf"
SAMPLE_2 = SAMPLES / "注文書_見本2_あさひ観光サービス.pdf"


def h(user=YAMADA, tenant=TENANT_A):
    return {"X-Tenant-Id": tenant, "X-Dev-User-Id": user}


def upload(client, data: bytes, name="注文書.pdf", user=YAMADA):
    return client.post(
        "/api/imports",
        json={"file_name": name, "content_base64": base64.b64encode(data).decode()},
        headers=h(user),
    )


def customer_id(client, name):
    return next(c["id"] for c in client.get("/api/customers", headers=h()).json() if c["name"] == name)


def draft_body(detail, **over):
    return {**detail["draft"], **over}


def warning_ids(detail):
    return [c["id"] for c in detail["checks"] if c["level"] == "warning"]


@pytest.fixture(scope="module", autouse=True)
def fake_ai():
    original = settings.ai_provider
    settings.ai_provider = "fake"
    yield
    settings.ai_provider = original


@pytest.fixture(scope="module")
def other_pdf():
    from weasyprint import HTML

    html = "<html lang='ja'><body style=\"font-family:'Noto Sans CJK JP','IPAexGothic'\"><h1>請求書</h1><p>ご請求金額 10,000円 お支払いは月末までにお願いします。</p></body></html>"
    return HTML(string=html).write_pdf()


def test_初期値はオフで管理者だけが切り替えられる(client):
    s = client.get("/api/imports/settings", headers=h()).json()
    assert s == {"available": True, "enabled": False, "provider": "見本用(fake)", "can_edit": True}
    assert upload(client, SAMPLE_1.read_bytes()).status_code == 403

    assert client.put("/api/imports/settings", json={"enabled": True}, headers=h(SUZUKI)).status_code == 403
    res = client.put("/api/imports/settings", json={"enabled": True}, headers=h())
    assert res.status_code == 200 and res.json()["enabled"] is True


def test_PDF以外は受け付けない(client):
    res = upload(client, b"hello", name="memo.txt")
    assert res.status_code == 422
    assert res.json()["detail"] == "PDFファイルを選んでください"


def test_見本1を取り込んで確定すると受注になる(client):
    res = upload(client, SAMPLE_1.read_bytes(), name=SAMPLE_1.name, user=SUZUKI)  # 一般の社員も取り込める
    assert res.status_code == 201
    detail = res.json()
    # 裏の処理(テストでは応答の前に終わる)で読み取りまで済んでいる
    detail = client.get(f"/api/imports/{detail['id']}", headers=h()).json()
    assert detail["status"] == "review"
    assert detail["provider"] == "fake" and detail["prompt_version"] == "order_v1" and detail["page_count"] == 1
    assert detail["issuer_name"] == "(株)みなと物産" and detail["doc_total"] == "28220"
    assert detail["draft"]["customer_order_ref"] == "SN-2026-0412"
    assert detail["draft"]["order_date"] == "2026-11-20"
    assert [l["tax_category"] for l in detail["draft"]["lines"]] == ["standard", "standard", "reduced"]

    # 取引先は候補を出すだけで、選ぶまでは確定できない
    top = detail["customer_candidates"][0]
    assert top["name"] == "株式会社みなと物産" and "名前が一致" in top["reasons"]
    assert [c["id"] for c in detail["checks"] if c["level"] == "error"] == ["customer_missing"]
    # 一覧では「取引先の選択待ち」として、ほかのエラーとは分けて数える
    assert detail["needs_customer"] is True and detail["error_count"] == 0
    assert set(warning_ids(detail)) <= {"order_date_future", "order_date_old"}  # 金額・税額は注文書と一致
    res = client.post(f"/api/imports/{detail['id']}/confirm", json={"draft": detail["draft"]}, headers=h())
    assert res.status_code == 422 and "取引先を選んでください" in res.json()["detail"]

    # 同じPDFは二重に取り込めない
    assert upload(client, SAMPLE_1.read_bytes()).status_code == 409

    draft = draft_body(detail, customer_id=top["customer_id"], title="事務所用品の追加発注(12月分)")
    saved = client.put(f"/api/imports/{detail['id']}/draft", json=draft, headers=h()).json()
    assert [c for c in saved["checks"] if c["level"] == "error"] == []

    res = client.post(
        f"/api/imports/{detail['id']}/confirm",
        json={"draft": saved["draft"], "acknowledged": warning_ids(saved)},
        headers=h(),
    )
    assert res.status_code == 200, res.text
    done = res.json()
    assert done["status"] == "confirmed" and done["order_number"]

    order = client.get(f"/api/orders/{done['order_id']}", headers=h()).json()
    assert order["customer_order_ref"] == "SN-2026-0412"
    assert order["source_import_id"] == detail["id"]
    assert order["title"] == "事務所用品の追加発注(12月分)"
    assert order["order_date"] == "2026-11-20" and order["delivery_due_date"] == "2026-12-10"
    assert order["total_amount"] == "28220" and order["tax_amount"] == "2460"
    assert [l["description"] for l in order["lines"]] == ["A4コピー用紙(500枚入)", "ボールペン 黒", "会議用お茶 500ml"]

    # 人が直した項目が記録される
    conn = client.db_conn
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        rows = conn.execute(
            "SELECT field, ai_value, final_value FROM app.document_import_corrections WHERE import_id = %s", (detail["id"],)
        ).fetchall()
    assert rows == [{"field": "title", "ai_value": "事務所用品の追加発注", "final_value": "事務所用品の追加発注(12月分)"}]

    # 確定した取り込みは直せない。同じ注文番号の受注は手入力でも作れない
    assert client.put(f"/api/imports/{detail['id']}/draft", json=draft, headers=h()).status_code == 409
    dup = client.post(
        "/api/orders",
        json={"customer_id": top["customer_id"], "customer_order_ref": "SN-2026-0412",
              "lines": [{"description": "追加", "quantity": "1", "unit_price": "100"}]},
        headers=h(),
    )
    assert dup.status_code == 409 and "SN-2026-0412" in dup.json()["detail"]


def test_見本2は読み違いを警告で知らせる(client):
    detail = upload(client, SAMPLE_2.read_bytes(), name=SAMPLE_2.name).json()
    detail = client.get(f"/api/imports/{detail['id']}", headers=h()).json()
    warnings = set(warning_ids(detail))
    assert {"line_amount:0", "total_mismatch:subtotal", "total_mismatch:tax_total", "total_mismatch:total",
            "ai_uncertain", "tax_assumed"} <= warnings
    assert detail["draft"]["delivery_due_date"] == ""

    cid = customer_id(client, "あさひ観光サービス")
    draft = draft_body(detail, customer_id=cid)
    # 警告を確認しないと確定できない
    res = client.post(f"/api/imports/{detail['id']}/confirm", json={"draft": draft, "acknowledged": []}, headers=h())
    assert res.status_code == 422 and "確認していない警告" in res.json()["detail"]

    # 単価を直すと、金額の警告が消える
    draft["lines"][0]["unit_price"] = "18.5"
    saved = client.put(f"/api/imports/{detail['id']}/draft", json=draft, headers=h()).json()
    remaining = set(warning_ids(saved))
    assert not any(w.startswith(("line_amount", "total_mismatch")) for w in remaining)

    done = client.post(
        f"/api/imports/{detail['id']}/confirm", json={"draft": saved["draft"], "acknowledged": list(remaining)}, headers=h()
    ).json()
    order = client.get(f"/api/orders/{done['order_id']}", headers=h()).json()
    assert order["total_amount"] == "92400"
    assert order["lines"][0]["unit_price"] == "18.50"

    conn = client.db_conn
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        fields = {r["field"]: (r["ai_value"], r["final_value"]) for r in conn.execute(
            "SELECT field, ai_value, final_value FROM app.document_import_corrections WHERE import_id = %s", (detail["id"],)
        ).fetchall()}
    assert fields == {"lines[1].unit_price": ("18", "18.5")}


def test_注文書でない書類は失敗にし注文書として読み直せる(client, other_pdf):
    detail = upload(client, other_pdf, name="請求書.pdf").json()
    detail = client.get(f"/api/imports/{detail['id']}", headers=h()).json()
    assert detail["status"] == "failed"
    assert "注文書ではない書類と判定されました" in detail["error_message"]
    assert detail["extracted"] is None

    # 注文書として読み直しても、fake には答えがないので失敗のまま
    again = client.post(f"/api/imports/{detail['id']}/retry", json={"as_order": True}, headers=h())
    assert again.status_code == 200
    after = client.get(f"/api/imports/{detail['id']}", headers=h()).json()
    assert after["status"] == "failed" and "決められた形" in after["error_message"]

    # 破棄すると、同じPDFをもう一度取り込める
    gone = client.post(f"/api/imports/{detail['id']}/discard", json={"reason": "請求書だった"}, headers=h()).json()
    assert gone["status"] == "discarded" and gone["discard_reason"] == "請求書だった"
    assert upload(client, other_pdf, name="請求書.pdf").status_code == 201


def test_一覧と会社ごとの分離(client):
    mine = client.get("/api/imports", headers=h()).json()
    assert len(mine) >= 4
    assert {i["status"] for i in client.get("/api/imports?filter=confirmed", headers=h()).json()} == {"confirmed"}
    assert client.get("/api/imports", headers=h(tenant=TENANT_B)).json() == []
    assert client.get(f"/api/imports/{mine[0]['id']}", headers=h(tenant=TENANT_B)).status_code == 404


def test_読み取り結果は書き換えられず記録は消せない(client):
    target = client.get("/api/imports?filter=confirmed", headers=h()).json()[0]
    conn = client.db_conn

    def denied(sql):
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('app.tenant_id', %s, true), set_config('app.user_id', %s, true)", (TENANT_A, YAMADA))
                conn.execute(sql, (target["id"],))
        except Exception as e:  # noqa: BLE001
            return str(e)
        return None

    assert "変更できません" in denied("UPDATE app.document_imports SET extracted = '{}' WHERE id = %s")
    assert denied("DELETE FROM app.document_imports WHERE id = %s") is not None
    assert denied("UPDATE app.document_import_corrections SET final_value = 'x' WHERE import_id = %s") is not None


def test_PDFを開ける(client):
    target = client.get("/api/imports", headers=h()).json()[-1]
    res = client.get(f"/api/imports/{target['id']}/pdf", headers=h())
    assert res.status_code == 200 and res.content.startswith(b"%PDF-")
