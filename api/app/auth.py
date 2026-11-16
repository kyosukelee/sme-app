"""利用者の特定と、選んだ会社への所属確認。"""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from psycopg import Connection

from app.config import settings
from app.db import scoped_transaction


def current_user_id() -> UUID:
    """ログイン中の利用者IDを返す。"""
    if settings.auth_mode == "dev":
        if settings.dev_user_id is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "DEV_USER_ID が設定されていません")
        return settings.dev_user_id
    # TODO: Authorization ヘッダーの Supabase Auth トークンを検証し、利用者IDを取り出す
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Supabase Auth の検証はまだ実装していません")


UserId = Annotated[UUID, Depends(current_user_id)]


def user_conn(user_id: UserId) -> Iterator[Connection]:
    """会社を選ぶ前の処理(所属している会社の一覧など)に使う接続。"""
    with scoped_transaction(user_id) as conn:
        yield conn


def tenant_conn(
    user_id: UserId,
    tenant_id: Annotated[UUID, Header(alias="X-Tenant-Id")],
) -> Iterator[Connection]:
    """画面で選んだ会社の接続。所属していない会社なら 403 にする。"""
    with scoped_transaction(user_id, tenant_id) as conn:
        row = conn.execute(
            "SELECT role FROM app.memberships WHERE tenant_id = %s AND user_id = %s",
            (tenant_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "この会社のデータを扱う権限がありません")
        yield conn


UserConn = Annotated[Connection, Depends(user_conn)]
TenantConn = Annotated[Connection, Depends(tenant_conn)]


def admin_conn(conn: TenantConn) -> Connection:
    """管理者だけが使える操作の接続。管理者でなければ 403 にする。"""
    row = conn.execute(
        "SELECT role FROM app.memberships WHERE tenant_id = app.current_tenant_id() AND user_id = app.current_user_id()"
    ).fetchone()
    if row is None or row["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "この操作は管理者だけが行えます")
    return conn


AdminConn = Annotated[Connection, Depends(admin_conn)]
