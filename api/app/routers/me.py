"""ログイン中の利用者に関するAPI。"""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.auth import UserConn, UserId
from app.config import settings

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
        LEFT JOIN app.member_profiles p ON p.tenant_id = m.tenant_id AND p.user_id = m.user_id
        WHERE m.user_id = app.current_user_id() AND coalesce(p.is_active, true)
        ORDER BY t.name
        """
    ).fetchall()


class SessionOut(BaseModel):
    user_id: UUID
    auth_mode: str  # dev のときは、画面で利用者を切り替えられる


@router.get("", response_model=SessionOut)
def me(user_id: UserId):
    return SessionOut(user_id=user_id, auth_mode=settings.auth_mode)
