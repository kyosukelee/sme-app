"""社員・担当役割と、開発用の利用者の切り替えのテスト。変更はテストの最後に取り消される(conftest.py)。"""

TENANT_A = "a1000000-0000-0000-0000-000000000001"
TENANT_B = "b2000000-0000-0000-0000-000000000002"
YAMADA = "22222222-0000-0000-0000-000000000001"  # 代表・管理者(開発用の既定の利用者)
SATO = "22222222-0000-0000-0000-000000000002"  # 営業部長(上長: 山田)
SUZUKI = "22222222-0000-0000-0000-000000000003"  # 営業部(上長: 佐藤)
TANAKA = "22222222-0000-0000-0000-000000000004"  # 経理(上長: 山田)
H = {"X-Tenant-Id": TENANT_A}


def as_user(user_id, tenant=TENANT_A):
    return {"X-Tenant-Id": tenant, "X-Dev-User-Id": user_id}


def member_body(m, **over):
    keys = ("display_name", "email", "department", "manager_user_id", "role", "is_active")
    return {k: m[k] for k in keys} | over


def get_member(client, user_id):
    return next(m for m in client.get("/api/members", params={"include_inactive": "true"}, headers=H).json()
                if m["user_id"] == user_id)


def test_社員の一覧(client):
    members = {m["display_name"]: m for m in client.get("/api/members", headers=H).json()}
    assert {"山田 太郎", "佐藤 花子", "鈴木 一郎", "田中 美咲"} <= set(members)
    assert members["鈴木 一郎"]["manager_name"] == "佐藤 花子"
    assert members["山田 太郎"]["is_me"] and members["山田 太郎"]["role"] == "admin"
    assert [g["name"] for g in members["田中 美咲"]["groups"]] == ["経理担当"]


def test_開発用に利用者を切り替えられる(client):
    me = client.get("/api/members/me", headers=as_user(SUZUKI)).json()
    assert (me["display_name"], me["role"], me["is_me"]) == ("鈴木 一郎", "member", True)
    assert client.get("/api/me", headers={"X-Dev-User-Id": SUZUKI}).json() == {"user_id": SUZUKI, "auth_mode": "dev"}
    # 一般の社員は社員情報を変えられない
    res = client.put(f"/api/members/{SUZUKI}", json=member_body(me, display_name="変更"), headers=as_user(SUZUKI))
    assert res.status_code == 403
    # 所属していない会社は使えない
    assert client.get("/api/members", headers=as_user(SUZUKI, TENANT_B)).status_code == 403
    assert [t["id"] for t in client.get("/api/me/tenants", headers={"X-Dev-User-Id": SUZUKI}).json()] == [TENANT_A]


def test_社員を追加する(client):
    res = client.post("/api/members", json={"display_name": " 高橋 誠 ", "email": "takahashi@example.com",
                                            "department": "営業部", "manager_user_id": SATO}, headers=H)
    assert res.status_code == 201, res.text
    m = res.json()
    assert (m["display_name"], m["manager_name"], m["role"], m["is_active"]) == ("高橋 誠", "佐藤 花子", "member", True)
    # 追加した社員として会社を使える
    assert client.get("/api/members/me", headers=as_user(m["user_id"])).json()["display_name"] == "高橋 誠"
    # 同じメールアドレスは使えない
    dup = client.post("/api/members", json={"display_name": "別人", "email": "TAKAHASHI@example.com"}, headers=H)
    assert dup.status_code == 409


def test_上長の関係が一周しない(client):
    sato = get_member(client, SATO)
    res = client.put(f"/api/members/{SATO}", json=member_body(sato, manager_user_id=SUZUKI), headers=H)
    assert res.status_code == 422 and "一周" in res.json()["detail"]
    res = client.put(f"/api/members/{SATO}", json=member_body(sato, manager_user_id=SATO), headers=H)
    assert res.status_code == 422


def test_管理者がいなくなる変更はできない(client):
    yamada = get_member(client, YAMADA)
    res = client.put(f"/api/members/{YAMADA}", json=member_body(yamada, role="member"), headers=H)
    assert res.status_code == 409 and "管理者" in res.json()["detail"]
    # ほかに管理者がいれば外せる
    sato = get_member(client, SATO)
    assert client.put(f"/api/members/{SATO}", json=member_body(sato, role="admin"), headers=H).status_code == 200
    assert client.put(f"/api/members/{YAMADA}", json=member_body(yamada, role="member"), headers=H).status_code == 200
    # 元に戻す(佐藤さんとして山田さんを管理者に戻す)
    assert client.put(f"/api/members/{YAMADA}", json=member_body(yamada, role="admin"), headers=as_user(SATO)).status_code == 200
    assert client.put(f"/api/members/{SATO}", json=member_body(sato, role="member"), headers=H).status_code == 200


def test_使用中止(client):
    sato = get_member(client, SATO)
    # 部下がいる社員は、先に上長を変えないと使用中止にできない
    res = client.put(f"/api/members/{SATO}", json=member_body(sato, is_active=False), headers=H)
    assert res.status_code == 409 and "鈴木 一郎さん" in res.json()["detail"]
    # 自分自身は使用中止にできない
    yamada = get_member(client, YAMADA)
    assert client.put(f"/api/members/{YAMADA}", json=member_body(yamada, is_active=False), headers=H).status_code == 409

    suzuki = get_member(client, SUZUKI)
    assert client.put(f"/api/members/{SUZUKI}", json=member_body(suzuki, is_active=False), headers=H).status_code == 200
    # 使用中止の社員は会社を使えず、会社の一覧にも出ない
    assert client.get("/api/members/me", headers=as_user(SUZUKI)).status_code == 403
    assert client.get("/api/me/tenants", headers={"X-Dev-User-Id": SUZUKI}).json() == []
    assert SUZUKI not in [m["user_id"] for m in client.get("/api/members", headers=H).json()]
    # 使用中止の社員は上長にできない
    tanaka = get_member(client, TANAKA)
    assert client.put(f"/api/members/{TANAKA}", json=member_body(tanaka, manager_user_id=SUZUKI), headers=H).status_code == 422
    client.put(f"/api/members/{SUZUKI}", json=member_body(suzuki, is_active=True), headers=H)


def test_担当役割(client):
    groups = {g["name"]: g for g in client.get("/api/approver-groups", headers=H).json()}
    assert [m["display_name"] for m in groups["代表"]["members"]] == ["山田 太郎"]

    res = client.post("/api/approver-groups", json={"name": "総務担当", "user_ids": [TANAKA, SATO]}, headers=H)
    assert res.status_code == 201, res.text
    group = res.json()
    assert sorted(m["display_name"] for m in group["members"]) == ["佐藤 花子", "田中 美咲"]
    assert client.post("/api/approver-groups", json={"name": "総務担当"}, headers=H).status_code == 409

    res = client.put(f"/api/approver-groups/{group['id']}", json={"name": "総務", "user_ids": [TANAKA]}, headers=H)
    assert [m["display_name"] for m in res.json()["members"]] == ["田中 美咲"]
    assert [g["name"] for g in get_member(client, TANAKA)["groups"]] == ["経理担当", "総務"]

    # 一般の社員は変えられない
    assert client.delete(f"/api/approver-groups/{group['id']}", headers=as_user(TANAKA)).status_code == 403
    assert client.delete(f"/api/approver-groups/{group['id']}", headers=H).status_code == 204
    # ほかの会社の社員は入れられない
    res = client.post("/api/approver-groups", json={"name": "他社", "user_ids": [YAMADA]}, headers={"X-Tenant-Id": TENANT_B})
    assert res.status_code == 403  # 広島テスト工業では一般の利用者


def test_ほかの会社の社員は見えない(client):
    names = [m["display_name"] for m in client.get("/api/members", headers={"X-Tenant-Id": TENANT_B}).json()]
    assert names == ["山田 太郎"]
