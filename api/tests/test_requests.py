"""ワークフロー(申請・承認)の API のテスト。変更はテストの最後に取り消される(conftest.py)。

開発用データの社員: 山田(代表・管理者)、佐藤(営業部、上長: 山田)、鈴木(営業部、上長: 佐藤)、田中(経理、上長: 山田)
担当役割: 代表 = 山田、経理担当 = 田中
"""

import base64

import pytest

TENANT_A = "a1000000-0000-0000-0000-000000000001"
YAMADA = "22222222-0000-0000-0000-000000000001"
SATO = "22222222-0000-0000-0000-000000000002"
SUZUKI = "22222222-0000-0000-0000-000000000003"
TANAKA = "22222222-0000-0000-0000-000000000004"


def as_user(user_id):
    return {"X-Tenant-Id": TENANT_A, "X-Dev-User-Id": user_id}


def types(client, who=SUZUKI):
    return {t["code"]: t for t in client.get("/api/request-types", headers=as_user(who)).json()}


def purchase(client, who=SUZUKI, amount="150000", **over):
    body = {
        "request_type_id": types(client)["purchase"]["id"],
        "title": "ノートPCの購入",
        "form_data": {"item_name": "ノートPC", "quantity": "1", "reason": "故障のため"},
        "amount": amount,
    } | over
    res = client.post("/api/requests", json=body, headers=as_user(who))
    assert res.status_code == 201, res.text
    return res.json()


def act(client, who, request_id, action, comment=None, expect=200):
    res = client.post(f"/api/requests/{request_id}/{action}", json={"comment": comment}, headers=as_user(who))
    assert res.status_code == expect, res.text
    return res.json()


def route(detail):
    return [(s["label"], s["status"], [a["display_name"] for a in s["approvers"]]) for s in detail["steps"]]


def test_申請種別の初期セット(client):
    t = types(client)
    assert [x["name"] for x in t.values()] == ["経費精算", "購買申請", "休暇申請", "汎用申請"]
    assert [(s["label"], s["min_amount"]) for s in t["purchase"]["steps"]] == [("上長", None), ("代表", "100000")]
    assert t["leave"]["amount_mode"] == "none"
    # 2回目以降は増えない
    assert len(types(client, YAMADA)) == 4


def test_申請する前に承認ルートを確かめられる(client):
    tid = types(client)["purchase"]["id"]
    small = client.post("/api/requests/route-preview", json={"request_type_id": tid, "amount": "30000"}, headers=as_user(SUZUKI)).json()
    assert [(s["label"], [a["display_name"] for a in s["approvers"]]) for s in small["steps"]] == [("上長", ["佐藤 花子"])]
    big = client.post("/api/requests/route-preview", json={"request_type_id": tid, "amount": "100000"}, headers=as_user(SUZUKI)).json()
    assert [s["label"] for s in big["steps"]] == ["上長", "代表"]
    own = client.post("/api/requests/route-preview", json={"request_type_id": tid, "amount": "500000"}, headers=as_user(YAMADA)).json()
    assert own["auto_approve"] is True


def test_承認の流れ(client):
    draft = purchase(client)
    assert draft["status"] == "draft" and draft["request_number"] is None
    assert draft["permissions"]["can_submit"] and not draft["permissions"]["can_comment"]

    before = client.get("/api/requests/summary", headers=as_user(SATO)).json()["todo"]
    r = act(client, SUZUKI, draft["id"], "submit")
    assert r["status"] == "pending" and r["request_number"].startswith("R-")
    assert route(r) == [("上長", "pending", ["佐藤 花子"]), ("代表", "waiting", ["山田 太郎"])]

    # 自分の番でない人は承認できない(管理者でも)
    act(client, YAMADA, r["id"], "approve", expect=403)
    # 承認待ちの一覧と件数
    todo = client.get("/api/requests", params={"box": "todo"}, headers=as_user(SATO)).json()
    mine = next(x for x in todo if x["id"] == r["id"])
    assert mine["is_my_turn"] and mine["current_step_label"] == "上長"
    assert client.get("/api/requests/summary", headers=as_user(SATO)).json()["todo"] == before + 1

    r = act(client, SATO, r["id"], "approve", "問題ありません")
    assert r["status"] == "pending" and r["current_step_label"] == "代表"
    act(client, SATO, r["id"], "approve", expect=403)  # 同じ人がもう一度は承認できない
    r = act(client, YAMADA, r["id"], "approve")
    assert r["status"] == "approved" and r["decided_at"]
    assert [e["action"] for e in r["events"]] == ["submit", "approve", "approve", "complete"]
    assert r["events"][1]["comment"] == "問題ありません" and r["events"][1]["actor"]["display_name"] == "佐藤 花子"

    # 承認済の申請は変えられない
    act(client, SUZUKI, r["id"], "withdraw", expect=409)
    res = client.put(f"/api/requests/{r['id']}", json={"request_type_id": r["request_type_id"], "title": "変更"},
                     headers=as_user(SUZUKI))
    assert res.status_code == 409


def test_差戻しと再申請(client):
    returned_before = client.get("/api/requests/summary", headers=as_user(SUZUKI)).json()["returned"]
    r = act(client, SUZUKI, purchase(client)["id"], "submit")
    act(client, SATO, r["id"], "return", expect=422)  # 理由は必須
    r = act(client, SATO, r["id"], "return", "見積書を添付してください")
    assert r["status"] == "returned"
    assert route(r) == [("上長", "returned", ["佐藤 花子"]), ("代表", "cancelled", ["山田 太郎"])]
    assert not r["permissions"]["can_edit"]  # 差し戻した佐藤さんからは直せない
    mine = client.get(f"/api/requests/{r['id']}", headers=as_user(SUZUKI)).json()
    assert mine["permissions"]["can_edit"] and mine["permissions"]["can_submit"] and not mine["permissions"]["can_delete"]
    assert client.get("/api/requests/summary", headers=as_user(SUZUKI)).json()["returned"] == returned_before + 1

    # 直して再申請すると、1段目からやり直す
    body = {"request_type_id": r["request_type_id"], "title": "ノートPCの購入(見積書添付)",
            "form_data": r["form_data"], "amount": "160000"}
    assert client.put(f"/api/requests/{r['id']}", json=body, headers=as_user(SUZUKI)).status_code == 200
    r2 = act(client, SUZUKI, r["id"], "submit")
    assert r2["round"] == 2 and r2["request_number"] == r["request_number"]
    assert route(r2) == [("上長", "pending", ["佐藤 花子"]), ("代表", "waiting", ["山田 太郎"])]
    assert [e["action"] for e in r2["events"]] == ["submit", "return", "resubmit"]


def test_却下と取下げ(client):
    r = act(client, SUZUKI, purchase(client)["id"], "submit")
    r = act(client, SATO, r["id"], "reject", "今期の予算がありません")
    assert r["status"] == "rejected"
    act(client, SUZUKI, r["id"], "submit", expect=409)  # 却下は終了で、再申請できない

    r = act(client, SUZUKI, purchase(client)["id"], "submit")
    act(client, SATO, r["id"], "withdraw", expect=403)  # 取り下げられるのは申請者だけ
    r = act(client, SUZUKI, r["id"], "withdraw")
    assert r["status"] == "withdrawn" and route(r)[0][1] == "cancelled"
    todo = client.get("/api/requests", params={"box": "todo"}, headers=as_user(SATO)).json()
    assert r["id"] not in [x["id"] for x in todo]


def test_本人の段の省略(client):
    # 経理担当の田中さんの経費精算: 上長(山田)だけが承認する
    t = types(client, TANAKA)["expense"]
    draft = client.post("/api/requests", json={
        "request_type_id": t["id"], "title": "交通費", "amount": "1200",
        "form_data": {"used_on": "2026-11-18", "payee": "JR西日本", "purpose": "客先訪問"},
    }, headers=as_user(TANAKA)).json()
    r = act(client, TANAKA, draft["id"], "submit")
    assert [(s["label"], s["status"], s["skip_reason"]) for s in r["steps"]] == [
        ("上長", "pending", None), ("経理担当", "skipped", "本人のため省略")]
    r = act(client, YAMADA, r["id"], "approve")
    assert r["status"] == "approved"

    # 代表の購買申請は、すべての段が省略されて申請と同時に承認済になる
    r = act(client, YAMADA, purchase(client, YAMADA, "500000")["id"], "submit")
    assert r["status"] == "approved"
    assert [e["action"] for e in r["events"]] == ["submit", "skip", "skip", "complete"]


def test_必須項目は申請するときに確かめる(client):
    tid = types(client)["leave"]["id"]
    draft = client.post("/api/requests", json={"request_type_id": tid, "title": "休暇", "form_data": {"date_from": "2026-12-01"}},
                        headers=as_user(SUZUKI))
    assert draft.status_code == 201  # 下書きは途中でも保存できる
    res = client.post(f"/api/requests/{draft.json()['id']}/submit", headers=as_user(SUZUKI))
    assert res.status_code == 422 and "種類" in res.json()["detail"]
    bad = client.post("/api/requests", json={"request_type_id": tid, "title": "休暇", "form_data": {"date_from": "明日"}},
                      headers=as_user(SUZUKI))
    assert bad.status_code == 422


def test_見られる範囲(client):
    r = act(client, SUZUKI, purchase(client, amount="20000")["id"], "submit")
    # ルートに入っていない田中さんは見られない(一覧にも出ない)
    assert client.get(f"/api/requests/{r['id']}", headers=as_user(TANAKA)).status_code == 404
    assert client.post(f"/api/requests/{r['id']}/comments", json={"comment": "x"}, headers=as_user(TANAKA)).status_code == 404
    # 承認者・管理者は見られる
    assert client.get(f"/api/requests/{r['id']}", headers=as_user(SATO)).status_code == 200
    assert client.get(f"/api/requests/{r['id']}", headers=as_user(YAMADA)).status_code == 200
    # すべての申請は管理者だけ
    assert client.get("/api/requests", params={"box": "all"}, headers=as_user(SUZUKI)).status_code == 403
    assert r["id"] in [x["id"] for x in client.get("/api/requests", params={"box": "all"}, headers=as_user(YAMADA)).json()]
    # ほかの人の下書きは管理者にも出ない
    draft = purchase(client)
    assert draft["id"] not in [x["id"] for x in client.get("/api/requests", params={"box": "all"}, headers=as_user(YAMADA)).json()]

    # DB の RLS でも絞り込まれる(API の確認を通らずに読んでも見えない)
    conn = client.db_conn
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true), set_config('app.user_id', %s, true)", (TENANT_A, TANAKA))
        assert conn.execute("SELECT count(*) AS n FROM app.requests WHERE id = %s", (r["id"],)).fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM app.request_steps WHERE request_id = %s", (r["id"],)).fetchone()["n"] == 0
        conn.execute("SELECT set_config('app.user_id', %s, true)", (SATO,))
        assert conn.execute("SELECT count(*) AS n FROM app.requests WHERE id = %s", (r["id"],)).fetchone()["n"] == 1


def test_コメント(client):
    r = act(client, SUZUKI, purchase(client)["id"], "submit")
    act(client, SATO, r["id"], "comments", "納期はいつですか", expect=200)
    r = act(client, SUZUKI, r["id"], "comments", "来週です")
    assert [(e["action"], e["actor"]["display_name"], e["comment"]) for e in r["events"][1:]] == [
        ("comment", "佐藤 花子", "納期はいつですか"), ("comment", "鈴木 一郎", "来週です")]
    act(client, SUZUKI, r["id"], "comments", " ", expect=422)


def test_添付(client):
    draft = purchase(client)
    pdf = base64.b64encode(b"%PDF-1.4 sample").decode()
    res = client.post(f"/api/requests/{draft['id']}/attachments",
                      json={"file_name": "見積書.pdf", "content_type": "application/pdf", "content_base64": pdf}, headers=as_user(SUZUKI))
    assert res.status_code == 201, res.text
    att = res.json()["attachments"][0]
    assert (att["file_name"], att["size_bytes"], att["uploaded_by_name"]) == ("見積書.pdf", 15, "鈴木 一郎")
    exe = client.post(f"/api/requests/{draft['id']}/attachments",
                      json={"file_name": "a.exe", "content_base64": pdf}, headers=as_user(SUZUKI))
    assert exe.status_code == 422

    r = act(client, SUZUKI, draft["id"], "submit")
    # 承認者は添付を開ける。承認が始まった後は添付を変えられない
    file = client.get(f"/api/requests/{r['id']}/attachments/{att['id']}", headers=as_user(SATO))
    assert file.status_code == 200 and file.content == b"%PDF-1.4 sample"
    assert file.headers["content-type"] == "application/pdf"
    assert client.delete(f"/api/requests/{r['id']}/attachments/{att['id']}", headers=as_user(SUZUKI)).status_code == 409
    assert client.get(f"/api/requests/{r['id']}/attachments/{att['id']}", headers=as_user(TANAKA)).status_code == 404


def test_下書きの削除(client):
    draft = purchase(client)
    assert client.delete(f"/api/requests/{draft['id']}", headers=as_user(SATO)).status_code in (403, 404)
    assert client.delete(f"/api/requests/{draft['id']}", headers=as_user(SUZUKI)).status_code == 204
    r = act(client, SUZUKI, purchase(client)["id"], "submit")
    assert client.delete(f"/api/requests/{r['id']}", headers=as_user(SUZUKI)).status_code == 409


def test_DBでも許されない変更を拒否する(client):
    r = act(client, SUZUKI, purchase(client)["id"], "submit")
    conn = client.db_conn

    def denied(sql, params, user=SUZUKI):
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('app.tenant_id', %s, true), set_config('app.user_id', %s, true)", (TENANT_A, user))
                conn.execute(sql, params)
        except Exception as e:  # noqa: BLE001
            return str(e)
        raise AssertionError(f"通ってしまった: {sql}")

    assert "内容を変更できません" in denied("UPDATE app.requests SET amount = 1 WHERE id = %s", (r["id"],))
    assert "承認者だけ" in denied(
        "UPDATE app.request_steps SET status = 'approved', acted_by = %s WHERE request_id = %s AND status = 'pending'",
        (SUZUKI, r["id"]))
    assert "本人として" in denied(
        "UPDATE app.request_steps SET status = 'approved', acted_by = %s WHERE request_id = %s AND status = 'pending'",
        (SATO, r["id"]), user=YAMADA)
    assert "承認ルートは変更できません" in denied(
        "UPDATE app.request_steps SET candidate_user_ids = ARRAY[%s::uuid] WHERE request_id = %s AND step_no = 1",
        (SUZUKI, r["id"]), user=YAMADA)
    assert "denied" in denied("DELETE FROM app.request_events WHERE request_id = %s", (r["id"],))
    assert "本人として作成" in denied(
        "INSERT INTO app.requests (tenant_id, request_type_id, applicant_id, title) VALUES (%s, %s, %s, 'なりすまし')",
        (TENANT_A, r["request_type_id"], SATO))


def test_申請種別の設定(client):
    t = types(client, YAMADA)["leave"]
    groups = {g["name"]: g["id"] for g in client.get("/api/approver-groups", headers=as_user(YAMADA)).json()}
    body = {"name": "休暇届", "description": None, "is_active": True, "steps": [
        {"approver_kind": "manager"},
        {"approver_kind": "group", "approver_group_id": groups["経理担当"]},
        {"approver_kind": "user", "approver_user_id": YAMADA, "min_amount": None},
    ]}
    # 一般の社員は変えられない
    assert client.put(f"/api/request-types/{t['id']}", json=body, headers=as_user(SUZUKI)).status_code == 403
    res = client.put(f"/api/request-types/{t['id']}", json=body, headers=as_user(YAMADA))
    assert res.status_code == 200, res.text
    assert [s["label"] for s in res.json()["steps"]] == ["上長", "経理担当", "山田 太郎"]
    assert client.put(f"/api/request-types/{t['id']}", json=body | {"steps": [body["steps"][0]] * 4},
                      headers=as_user(YAMADA)).status_code == 422
    assert client.put(f"/api/request-types/{t['id']}", json=body | {"name": "経費精算"},
                      headers=as_user(YAMADA)).status_code == 409

    # 使わないにした種別は一般の社員の一覧に出ず、申請もできない
    client.put(f"/api/request-types/{t['id']}", json=body | {"is_active": False}, headers=as_user(YAMADA))
    assert "leave" not in types(client)
    res = client.post("/api/requests", json={"request_type_id": t["id"], "title": "休暇"}, headers=as_user(SUZUKI))
    assert res.status_code == 422

    # 承認の段で使っている担当役割は削除できない
    res = client.delete(f"/api/approver-groups/{groups['経理担当']}", headers=as_user(YAMADA))
    assert res.status_code == 409 and "経費精算" in res.json()["detail"]


def test_担当役割に社員がいないと申請できない(client):
    groups = {g["name"]: g for g in client.get("/api/approver-groups", headers=as_user(YAMADA)).json()}
    acc = groups["経理担当"]
    client.put(f"/api/approver-groups/{acc['id']}", json={"name": acc["name"], "user_ids": []}, headers=as_user(YAMADA))
    t = types(client)["expense"]
    draft = client.post("/api/requests", json={
        "request_type_id": t["id"], "title": "交通費", "amount": "800",
        "form_data": {"used_on": "2026-11-18", "payee": "広電", "purpose": "訪問"},
    }, headers=as_user(SUZUKI)).json()
    res = client.post(f"/api/requests/{draft['id']}/submit", headers=as_user(SUZUKI))
    assert res.status_code == 422 and "経理担当" in res.json()["detail"]
    client.put(f"/api/approver-groups/{acc['id']}", json={"name": acc["name"], "user_ids": [TANAKA]}, headers=as_user(YAMADA))


@pytest.mark.parametrize("box", ["todo", "mine"])
def test_一覧は誰でも使える(client, box):
    assert client.get("/api/requests", params={"box": box}, headers=as_user(TANAKA)).status_code == 200
