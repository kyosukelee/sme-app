"""利用者の特定と、選んだ会社への所属確認。"""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from psycopg import Connection

from app.config import settings
from app.db import scoped_transaction


def current_user_id(
    dev_user: Annotated[UUID | None, Header(alias="X-Dev-User-Id")] = None,
) -> UUID:
    """ログイン中の利用者IDを返す。

    開発モード(AUTH_MODE=dev)では DEV_USER_ID の利用者として扱う。画面の「利用者を切り替える(開発用)」で
    X-Dev-User-Id ヘッダーが付いていれば、その利用者として扱う(承認ルートを複数の人で試すため)。
    このヘッダーは開発モードのときだけ読み、本番では無視する。
    """
    if settings.auth_mode == "dev":
        if dev_user is not None:
            return dev_user
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
            """
            SELECT m.role, coalesce(p.is_active, true) AS is_active
            FROM app.memberships m
            LEFT JOIN app.member_profiles p ON p.tenant_id = m.tenant_id AND p.user_id = m.user_id
            WHERE m.tenant_id = %s AND m.user_id = %s
            """,
            (tenant_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "この会社のデータを扱う権限がありません")
        if not row["is_active"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "この会社では利用を停止されています。管理者に確認してください")
        yield conn


# scope="function": 応答を返す前にトランザクションを確定(コミット)する。
# 既定("request")のままだと応答を返した後にコミットされるため、保存の直後に次の操作が届くと
# まだ反映されていないデータを読むことがある(作成した申請種別が見つからない、など)。
# コミットに失敗したときも、成功の応答を返してしまわずにエラーにできる
UserConn = Annotated[Connection, Depends(user_conn, scope="function")]
TenantConn = Annotated[Connection, Depends(tenant_conn, scope="function")]


def admin_conn(conn: TenantConn) -> Connection:
    """管理者だけが使える操作の接続。管理者でなければ 403 にする。"""
    row = conn.execute(
        "SELECT role FROM app.memberships WHERE tenant_id = app.current_tenant_id() AND user_id = app.current_user_id()"
    ).fetchone()
    if row is None or row["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "この操作は管理者だけが行えます")
    return conn


AdminConn = Annotated[Connection, Depends(admin_conn)]
