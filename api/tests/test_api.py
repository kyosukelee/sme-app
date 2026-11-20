"""API と DB をつないだテスト。docker compose で DB が動いているときに実行する。

開発用のサンプルデータ(db/dev/900_dev_setup.sql)を前提にする。
変更はテストの最後に取り消される(conftest.py)。
"""

TENANT_A = "a1000000-0000-0000-0000-000000000001"  # サンプル商事
TENANT_B = "b2000000-0000-0000-0000-000000000002"  # テスト工業


def test_ヘルスチェック(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_所属している会社が2社見える(client):
    names = {t["name"] for t in client.get("/api/me/tenants").json()}
    assert names == {"サンプル商事", "テスト工業"}


def test_選んだ会社の取引先だけが返る(client):
    a = {c["name"] for c in client.get("/api/customers", headers={"X-Tenant-Id": TENANT_A}).json()}
    b = {c["name"] for c in client.get("/api/customers", headers={"X-Tenant-Id": TENANT_B}).json()}

    assert "株式会社みなと物産" in a
    assert "ひかり精機株式会社" in b
    assert a.isdisjoint(b)


def test_所属していない会社は403(client):
    res = client.get("/api/customers", headers={"X-Tenant-Id": "99999999-0000-0000-0000-000000000000"})
    assert res.status_code == 403


def test_会社を選ばないと422(client):
    assert client.get("/api/customers").status_code == 422


def test_名前が空の取引先は登録できない(client):
    res = client.post("/api/customers", json={"name": ""}, headers={"X-Tenant-Id": TENANT_A})
    assert res.status_code == 422
