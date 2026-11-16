"""DB を使うテストの準備。

テスト全体を1つのトランザクションの中で動かし、最後に取り消す。
API の中のトランザクションは入れ子(セーブポイント)になるので、開発用のデータは書き換わらない。
"""

import os
import tempfile
from contextlib import contextmanager

import pytest

# 控えPDFはテストごとの一時フォルダに保存する(設定を読み込む前に指定する)
os.environ.setdefault("DOCUMENT_DIR", tempfile.mkdtemp(prefix="sme-docs-"))
os.environ.setdefault("AUTH_MODE", "dev")  # テストは開発用ログイン(DEV_USER_ID)で動かす


class _SharedConnectionPool:
    """すべてのリクエストに同じ接続を渡す、テスト用の接続プール。"""

    def __init__(self, conn):
        self._conn = conn

    def open(self, *args, **kwargs):
        pass

    def close(self, *args, **kwargs):
        pass

    @contextmanager
    def connection(self, *args, **kwargs):
        yield self._conn


@pytest.fixture(scope="module")
def client():
    if "DATABASE_URL" not in os.environ:
        pytest.skip("DATABASE_URL がないため DB を使うテストを飛ばします")

    import psycopg
    from fastapi.testclient import TestClient
    from psycopg.rows import dict_row

    import app.db
    import app.main

    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    conn.execute("SELECT 1")  # ここで外側のトランザクションが始まる
    fake = _SharedConnectionPool(conn)
    originals = (app.db.pool, app.main.pool)
    app.db.pool = app.main.pool = fake
    try:
        with TestClient(app.main.app) as c:
            c.db_conn = conn  # DB を直接操作するテスト用(トリガーの確認など)
            yield c
    finally:
        conn.rollback()
        conn.close()
        app.db.pool, app.main.pool = originals
