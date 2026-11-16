"""ログイン中の利用者に関するAPI。"""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.auth import UserConn

router = APIRouter(prefix="/api/me", tags=["me"])


class TenantOut(BaseModel):
    id: UUID
    name: str
    role: str


@router.get("/tenants", response_model=list[TenantOut])
def list_my_tenants(conn: UserConn):
    """所属している会社の一覧(ログイン直後の会社選択に使う)。"""
    return conn.execute(
        """
        SELECT t.id, t.name, m.role
        FROM app.memberships m
        JOIN app.tenants t ON t.id = m.tenant_id
        WHERE m.user_id = app.current_user_id()
        ORDER BY t.name
        """
    ).fetchall()
