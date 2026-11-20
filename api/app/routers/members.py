"""社員(利用者)と担当役割。

ワークフローの承認ルートは「申請者の上長」「担当役割(経理担当・代表など)」「特定の人」で決めるため、
社員ごとに上長と所属する担当役割を持たせる。登録・変更は管理者だけ。

社員を追加すると、その会社の利用者(memberships)も作る。利用者ID(user_id)はアプリ側の ID で、
ログイン(Supabase Auth)を入れるときに認証の ID と対応付ける。それまでは開発用の「利用者を切り替える」で試す。
"""

import uuid
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from psycopg import Connection, errors
from pydantic import BaseModel, Field, field_validator

from app.auth import AdminConn, TenantConn
from app.fields import OptionalHalfwidthStr

router = APIRouter(tags=["members"])


# ---------------------------------------------------------------------
# 社員
# ---------------------------------------------------------------------
class MemberIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    email: OptionalHalfwidthStr = Field(default=None, max_length=254)
    department: str | None = Field(default=None, max_length=50)
    manager_user_id: UUID | None = None
    role: Literal["admin", "member"] = "member"
    is_active: bool = True

    @field_validator("display_name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("氏名を入力してください")
        return v

    @field_validator("email", "department")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        return (v or "").strip() or None

    @field_validator("email")
    @classmethod
    def email_shape(cls, v: str | None) -> str | None:
        if v and ("@" not in v or v.startswith("@") or v.endswith("@")):
            raise ValueError("メールアドレスの形が正しくありません")
        return v


class GroupRef(BaseModel):
    id: UUID
    name: str


class MemberOut(BaseModel):
    user_id: UUID
    display_name: str
    email: str | None
    department: str | None
    manager_user_id: UUID | None
    manager_name: str | None
    role: Literal["admin", "member"]
    is_active: bool
    groups: list[GroupRef]
    is_me: bool


MEMBER_SELECT = """
    SELECT p.user_id, p.display_name, p.email, p.department, p.manager_user_id, mp.display_name AS manager_name,
           m.role, p.is_active, p.user_id = app.current_user_id() AS is_me,
           coalesce((SELECT json_agg(json_build_object('id', g.id, 'name', g.name) ORDER BY g.name)
                     FROM app.approver_group_members gm JOIN app.approver_groups g ON g.id = gm.group_id
                     WHERE gm.tenant_id = p.tenant_id AND gm.user_id = p.user_id), '[]') AS groups
    FROM app.member_profiles p
    JOIN app.memberships m ON m.tenant_id = p.tenant_id AND m.user_id = p.user_id
    LEFT JOIN app.member_profiles mp ON mp.tenant_id = p.tenant_id AND mp.user_id = p.manager_user_id
    WHERE p.tenant_id = app.current_tenant_id()
"""


def _get(conn: Connection, user_id: UUID) -> dict:
    row = conn.execute(f"{MEMBER_SELECT} AND p.user_id = %s", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "社員が見つかりません")
    return row


@router.get("/api/members", response_model=list[MemberOut])
def list_members(conn: TenantConn, include_inactive: bool = False):
    return conn.execute(
        f"{MEMBER_SELECT} AND (p.is_active OR %s) ORDER BY p.is_active DESC, p.department NULLS LAST, p.display_name",
        (include_inactive,),
    ).fetchall()


@router.get("/api/members/me", response_model=MemberOut)
def my_profile(conn: TenantConn):
    return _get(conn, conn.execute("SELECT app.current_user_id() AS id").fetchone()["id"])


def _check_manager(conn: Connection, user_id: UUID | None, manager_id: UUID | None) -> None:
    """上長が同じ会社の使用中の社員で、上長の関係が一周しないことを確かめる。"""
    if manager_id is None:
        return
    if manager_id == user_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "自分自身を上長にはできません")
    seen: set[UUID] = set()
    current: UUID | None = manager_id
    while current is not None:
        row = conn.execute(
            "SELECT manager_user_id, is_active, display_name FROM app.member_profiles "
            "WHERE tenant_id = app.current_tenant_id() AND user_id = %s",
            (current,),
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "上長に選んだ社員が見つかりません")
        if current == manager_id and not row["is_active"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{row['display_name']}さんは使用中止のため、上長にできません")
        if current == user_id or current in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "上長の関係が一周してしまいます(部下を上長にすることはできません)",
            )
        seen.add(current)
        current = row["manager_user_id"]


def _write_profile(conn: Connection, sql: str, params: tuple) -> None:
    try:
        with conn.transaction():
            conn.execute(sql, params)
    except errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "このメールアドレスは、ほかの社員で使われています") from None


@router.post("/api/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(body: MemberIn, conn: AdminConn):
    """社員を追加する(その会社の利用者も作る)。"""
    user_id = uuid.uuid4()
    _check_manager(conn, user_id, body.manager_user_id)
    conn.execute(
        "INSERT INTO app.memberships (tenant_id, user_id, role) VALUES (app.current_tenant_id(), %s, %s)",
        (user_id, body.role),
    )
    _write_profile(
        conn,
        """
        INSERT INTO app.member_profiles (tenant_id, user_id, display_name, email, department, manager_user_id, is_active)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s)
        """,
        (user_id, body.display_name, body.email, body.department, body.manager_user_id, body.is_active),
    )
    return _get(conn, user_id)


@router.put("/api/members/{user_id}", response_model=MemberOut)
def update_member(user_id: UUID, body: MemberIn, conn: AdminConn):
    current = _get(conn, user_id)
    _check_manager(conn, user_id, body.manager_user_id)

    losing_admin = current["role"] == "admin" and current["is_active"] and (body.role != "admin" or not body.is_active)
    if losing_admin:
        others = conn.execute(
            """
            SELECT count(*) AS n FROM app.memberships m
            JOIN app.member_profiles p ON p.tenant_id = m.tenant_id AND p.user_id = m.user_id
            WHERE m.tenant_id = app.current_tenant_id() AND m.role = 'admin' AND p.is_active AND m.user_id <> %s
            """,
            (user_id,),
        ).fetchone()["n"]
        if others == 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "管理者がいなくなるため変更できません。先にほかの社員を管理者にしてください")
    if current["is_me"] and not body.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "自分自身を使用中止にはできません")
    if current["is_active"] and not body.is_active:
        subordinates = conn.execute(
            "SELECT display_name FROM app.member_profiles "
            "WHERE tenant_id = app.current_tenant_id() AND manager_user_id = %s AND is_active ORDER BY display_name",
            (user_id,),
        ).fetchall()
        if subordinates:
            names = "、".join(f"{s['display_name']}さん" for s in subordinates)
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"{names}の上長になっています。先に上長を変えてから使用中止にしてください"
            )

    conn.execute(
        "UPDATE app.memberships SET role = %s WHERE tenant_id = app.current_tenant_id() AND user_id = %s",
        (body.role, user_id),
    )
    _write_profile(
        conn,
        """
        UPDATE app.member_profiles
        SET display_name = %s, email = %s, department = %s, manager_user_id = %s, is_active = %s
        WHERE tenant_id = app.current_tenant_id() AND user_id = %s
        """,
        (body.display_name, body.email, body.department, body.manager_user_id, body.is_active, user_id),
    )
    return _get(conn, user_id)


# ---------------------------------------------------------------------
# 担当役割
# ---------------------------------------------------------------------
class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    user_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("担当役割の名前を入力してください")
        return v


class GroupMember(BaseModel):
    user_id: UUID
    display_name: str
    is_active: bool


class GroupOut(BaseModel):
    id: UUID
    name: str
    members: list[GroupMember]


def _groups(conn: Connection, group_id: UUID | None = None) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT g.id, g.name,
               coalesce((SELECT json_agg(json_build_object('user_id', p.user_id, 'display_name', p.display_name,
                                                           'is_active', p.is_active) ORDER BY p.display_name)
                         FROM app.approver_group_members gm
                         JOIN app.member_profiles p ON p.tenant_id = gm.tenant_id AND p.user_id = gm.user_id
                         WHERE gm.group_id = g.id), '[]') AS members
        FROM app.approver_groups g
        {"WHERE g.id = %s" if group_id else ""}
        ORDER BY g.name
        """,
        (group_id,) if group_id else (),
    ).fetchall()
    if group_id and not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "担当役割が見つかりません")
    return rows


def _set_group_members(conn: Connection, group_id: UUID, user_ids: list[UUID]) -> None:
    ids = list(dict.fromkeys(user_ids))
    found = conn.execute(
        "SELECT count(*) AS n FROM app.member_profiles WHERE tenant_id = app.current_tenant_id() AND user_id = ANY(%s::uuid[])",
        (ids,),
    ).fetchone()["n"]
    if found != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "選んだ社員の中に、見つからない人がいます")
    conn.execute("DELETE FROM app.approver_group_members WHERE group_id = %s", (group_id,))
    for uid in ids:
        conn.execute(
            "INSERT INTO app.approver_group_members (tenant_id, group_id, user_id) VALUES (app.current_tenant_id(), %s, %s)",
            (group_id, uid),
        )


def _save_group(conn: Connection, sql: str, params: tuple) -> dict | None:
    try:
        with conn.transaction():
            return conn.execute(sql, params).fetchone()
    except errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, f"担当役割「{params[0]}」はすでにあります") from None


@router.get("/api/approver-groups", response_model=list[GroupOut])
def list_groups(conn: TenantConn):
    return _groups(conn)


@router.post("/api/approver-groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupIn, conn: AdminConn):
    row = _save_group(
        conn, "INSERT INTO app.approver_groups (tenant_id, name) VALUES (app.current_tenant_id(), %s) RETURNING id",
        (body.name,),
    )
    _set_group_members(conn, row["id"], body.user_ids)
    return _groups(conn, row["id"])[0]


@router.put("/api/approver-groups/{group_id}", response_model=GroupOut)
def update_group(group_id: UUID, body: GroupIn, conn: AdminConn):
    row = _save_group(conn, "UPDATE app.approver_groups SET name = %s WHERE id = %s RETURNING id", (body.name, group_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "担当役割が見つかりません")
    _set_group_members(conn, group_id, body.user_ids)
    return _groups(conn, group_id)[0]


@router.delete("/api/approver-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: UUID, conn: AdminConn):
    used = conn.execute(
        """
        SELECT DISTINCT t.name FROM app.route_steps s JOIN app.request_types t ON t.id = s.request_type_id
        WHERE s.approver_group_id = %s ORDER BY t.name
        """,
        (group_id,),
    ).fetchall()
    if used:
        names = "、".join(f"「{u['name']}」" for u in used)
        raise HTTPException(status.HTTP_409_CONFLICT, f"申請種別{names}の承認の段で使っているため削除できません。先に承認の段を直してください")
    if conn.execute("DELETE FROM app.approver_groups WHERE id = %s RETURNING id", (group_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "担当役割が見つかりません")
