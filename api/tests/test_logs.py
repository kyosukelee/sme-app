"""操作の記録(変更履歴の対象・閲覧と書き出しの記録・アプリのログ・管理者の履歴画面の API)のテスト。"""

import base64
import json
import logging

import pytest

from app import logs

TENANT_A = "a1000000-0000-0000-0000-000000000001"
YAMADA = "22222222-0000-0000-0000-000000000001"  # 管理者
SATO = "22222222-0000-0000-0000-000000000002"
SUZUKI = "22222222-0000-0000-0000-000000000003"
H = {"X-Tenant-Id": TENANT_A}


def as_user(user_id):
    return {"X-Tenant-Id": TENANT_A, "X-Dev-User-Id": user_id}


def changes(client, **params):
    res = client.get("/api/logs/changes", params=params, headers=H)
    assert res.status_code == 200, res.text
    return res.json()


def activities(client, **params):
    res = client.get("/api/logs/activities", params=params, headers=H)
    assert res.status_code == 200, res.text
    return res.json()["items"]


def test_会社の設定の変更が履歴に残る(client):
    company = client.get("/api/company", headers=H).json()
    res = client.put("/api/company", json={"profile": company["profile"], "terms": company["terms"], "bank_fee_limit": 660},
                     headers=H)
    assert res.status_code == 200, res.text
    latest = changes(client, table="tenant_settings")["items"][0]
    assert (latest["table_label"], latest["record_label"], latest["action_label"]) == ("会社の設定", "会社の設定", "変更")
    assert {"field": "bank_fee_limit", "label": "振込手数料の上限", "before": "880", "after": "660"} in latest["changes"]
    assert latest["actor_name"] == "山田 太郎"
    # 変更履歴とアプリのログを、リクエストID で突き合わせられる
    assert latest["request_id"] == res.headers["X-Request-ID"]
    # 同じ内容で保存し直しても、変わっていないものは記録しない(自社情報・締め日など)
    same = client.put("/api/company", json={"profile": company["profile"], "terms": company["terms"], "bank_fee_limit": 660},
                      headers=H)
    assert all(i["request_id"] != same.headers["X-Request-ID"] for i in changes(client)["items"])


def test_権限の変更が履歴に残る(client):
    sato = next(m for m in client.get("/api/members", headers=H).json() if m["user_id"] == SATO)
    body = {k: sato[k] for k in ("display_name", "email", "department", "manager_user_id", "is_active")}
    assert client.put(f"/api/members/{SATO}", json=body | {"role": "admin"}, headers=H).status_code == 200
    latest = changes(client, table="memberships")["items"][0]
    assert latest["record_id"] == SATO and latest["record_label"] == "佐藤 花子"
    assert latest["changes"] == [{"field": "role", "label": "権限", "before": "一般", "after": "管理者"}]
    # 同じ社員の履歴だけを絞り込める
    mine = changes(client, record_id=SATO)["items"]
    assert mine and all(i["record_id"] == SATO for i in mine)
    client.put(f"/api/members/{SATO}", json=body | {"role": "member"}, headers=H)


def test_担当役割の社員の入れ替えが履歴に残る(client):
    group = next(g for g in client.get("/api/approver-groups", headers=H).json() if g["name"] == "経理担当")
    ids = [m["user_id"] for m in group["members"]]
    client.put(f"/api/approver-groups/{group['id']}", json={"name": group["name"], "user_ids": [*ids, SUZUKI]}, headers=H)
    added = [i for i in changes(client, table="approver_group_members")["items"] if i["action"] == "insert"]
    assert added[0]["record_label"] == "経理担当 ・ 鈴木 一郎"
    client.put(f"/api/approver-groups/{group['id']}", json={"name": group["name"], "user_ids": ids}, headers=H)


def test_取引先の変更は項目ごとに前後が分かる(client):
    c = client.post("/api/customers", json={"name": "履歴テスト商事", "honorific": "御中", "contact_name": None, "address": None,
                                            "email": None, "withholding_default": False, "terms": None, "is_active": True},
                    headers=H).json()
    body = {k: c[k] for k in ("name", "honorific", "contact_name", "address", "email", "withholding_default", "terms",
                              "is_active", "payer_names")}
    client.put(f"/api/customers/{c['id']}", json=body | {"name": "履歴テスト商事(新)", "is_active": False}, headers=H)
    items = changes(client, record_id=c["id"])["items"]
    assert [i["action_label"] for i in items] == ["変更", "追加"]
    assert {(x["label"], x["before"], x["after"]) for x in items[0]["changes"]} == {
        ("名前", "履歴テスト商事", "履歴テスト商事(新)"), ("使用中", "はい", "いいえ")}
    assert items[0]["record_label"] == "履歴テスト商事(新)"


def test_続きを読み込める(client):
    first = changes(client, limit=3)
    assert len(first["items"]) == 3 and first["next_before_id"] == first["items"][-1]["id"]
    second = changes(client, limit=3, before_id=first["next_before_id"])
    assert second["items"][0]["id"] < first["items"][-1]["id"]


def test_データの書き出しと控えを開いた記録(client):
    assert client.get("/api/export", headers=H).status_code == 200
    latest = activities(client)[0]
    assert (latest["action"], latest["actor_name"]) == ("export_all", "山田 太郎")

    customer = client.get("/api/customers", headers=H).json()[0]
    quote = client.post("/api/quotes", json={"customer_id": customer["id"], "lines": [
        {"description": "記録テスト", "quantity": "1", "unit": "式", "unit_price": "1000", "tax_category": "standard"}]},
        headers=H).json()
    client.get(f"/api/quotes/{quote['id']}/pdf", headers=H)  # 下書きのプレビューは記録しない
    assert activities(client)[0]["action"] == "export_all"
    issued = client.post(f"/api/quotes/{quote['id']}/issue", headers=H).json()
    assert client.get(f"/api/quotes/{quote['id']}/pdf", headers=H).status_code == 200
    latest = activities(client, action="open_document")[0]
    assert latest["summary"] == f"見積書 {issued['quote_number']}" and latest["target_id"] == quote["id"]


def test_申請の添付を開いた記録(client):
    t = next(x for x in client.get("/api/request-types", headers=as_user(SUZUKI)).json() if x["code"] == "general")
    r = client.post("/api/requests", json={"request_type_id": t["id"], "title": "添付の記録", "form_data": {"body": "本文"}},
                    headers=as_user(SUZUKI)).json()
    r = client.post(f"/api/requests/{r['id']}/attachments", json={
        "file_name": "資料.pdf", "content_type": "application/pdf", "content_base64": base64.b64encode(b"%PDF-1.4 x").decode()},
        headers=as_user(SUZUKI)).json()
    client.get(f"/api/requests/{r['id']}/attachments/{r['attachments'][0]['id']}", headers=as_user(SUZUKI))
    latest = activities(client, action="open_attachment")[0]
    assert latest["actor_name"] == "鈴木 一郎" and "資料.pdf" in latest["summary"]


def test_履歴は管理者だけが見られ記録は変えられない(client):
    assert client.get("/api/logs/changes", headers=as_user(SUZUKI)).status_code == 403
    assert client.get("/api/logs/activities", headers=as_user(SUZUKI)).status_code == 403
    assert client.get("/api/logs/changes", params={"table": "audit_logs"}, headers=H).status_code == 422
    conn = client.db_conn
    with pytest.raises(Exception, match="denied|追記専用"):
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            conn.execute("DELETE FROM app.activity_logs")


def test_リクエストIDとアプリのログ(client):
    records: list[logging.LogRecord] = []

    class Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Catch()
    logs.logger.addHandler(handler)
    try:
        res = client.get("/api/customers", headers=H | {"X-Request-ID": "test-request-0001"})
        assert res.headers["X-Request-ID"] == "test-request-0001"
        assert client.get("/api/logs/changes", headers=as_user(SUZUKI)).status_code == 403
    finally:
        logs.logger.removeHandler(handler)
    lines = [json.loads(logs.JsonFormatter().format(r)) for r in records if r.getMessage() == "request"]
    ok = next(x for x in lines if x["path"] == "/api/customers")
    assert (ok["request_id"], ok["status"], ok["method"], ok["tenant_id"], ok["user_id"]) == (
        "test-request-0001", 200, "GET", TENANT_A, YAMADA)
    denied = next(x for x in lines if x["path"] == "/api/logs/changes")
    assert denied["level"] == "WARNING" and denied["user_id"] == SUZUKI
    # 形の正しくないリクエストID は使わずに振り直す
    assert client.get("/api/health", headers={"X-Request-ID": "bad id!"}).headers["X-Request-ID"] != "bad id!"


def test_想定外のエラーはリクエストIDだけを返す(client):
    import app.main

    def boom():
        raise RuntimeError("内部の詳細")

    app.main.app.add_api_route("/api/_test_boom", boom)
    try:
        res = client.get("/api/_test_boom")
    finally:
        app.main.app.router.routes.pop()
    assert res.status_code == 500
    body = res.json()
    assert body["request_id"] == res.headers["X-Request-ID"] and body["request_id"] in body["detail"]
    assert "内部の詳細" not in res.text


def test_明細の行は指定したときだけ出す(client):
    customer = client.get("/api/customers", headers=H).json()[0]
    quote = client.post("/api/quotes", json={"customer_id": customer["id"], "lines": [
        {"description": "明細の記録", "quantity": "2", "unit": "個", "unit_price": "500", "tax_category": "standard"}]},
        headers=H).json()
    assert all(i["table_name"] != "quote_lines" for i in changes(client, limit=20)["items"])
    lines = changes(client, include_lines="true", limit=20)["items"]
    line = next(i for i in lines if i["table_name"] == "quote_lines")
    assert line["record_label"] == "1行目 明細の記録"
    assert {"field": "unit_price", "label": "単価", "before": None, "after": "500"} in line["changes"]
    assert {"field": "tax_rate", "label": "税率", "before": None, "after": "10%"} in line["changes"]
    assert any(i["table_name"] == "quotes" and i["record_id"] == quote["id"] for i in lines)
