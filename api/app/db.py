"""DB 接続。リクエストごとに1つのトランザクションを張り、最初に利用者と会社を設定する。

RLS はセッション変数 app.user_id / app.tenant_id を見て行を絞り込む。
set_config(..., true) は SET LOCAL と同じで、トランザクションが終われば元に戻るため、
接続をプールで使い回しても前のリクエストの会社が残ることはない。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=1,
    max_size=5,
    open=False,
    kwargs={"row_factory": dict_row},
)


@contextmanager
def scoped_transaction(user_id: UUID, tenant_id: UUID | None = None) -> Iterator[Connection]:
    """利用者(と会社)を設定したトランザクションを開く。例外が起きれば全体を取り消す。"""
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
            if tenant_id is not None:
                conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            yield conn
