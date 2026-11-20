"""ワークフロー(申請・承認)。

申請は「下書き → 承認待ち → 承認済/却下」を基本に、差戻しと取下げを加えた6つの状態で管理する。
承認ルートは申請した時点で申請種別の設定から組み立て、申請側(request_steps)へコピーする。
状態を変える操作は「POST /api/requests/{id}/動詞」の形にし、1つのトランザクションで
申請・各段・履歴をまとめて書く。決まりごとの判定は domain/workflow.py にある。

申請を見られるのは申請者・承認ルートに入っている社員・管理者だけ。API で確かめたうえで、
DB の RLS(request_visibility)でも同じ範囲に絞っている。
"""

import base64
import binascii
import mimetypes
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from psycopg import Connection, errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app import storage
from app.auth import AdminConn, TenantConn
from app.clock import today
from app.domain import workflow as wf

router = APIRouter(tags=["requests"])

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif"}


def _error(e: wf.WorkflowError, code: int = status.HTTP_409_CONFLICT) -> HTTPException:
    return HTTPException(code, str(e))


def _me(conn: Connection) -> tuple[UUID, bool]:
    row = conn.execute("SELECT app.current_user_id() AS id, app.current_user_is_admin() AS admin").fetchone()
    return row["id"], row["admin"]


# ---------------------------------------------------------------------
# 申請種別(初期セットの用意と、承認の段の設定)
# ---------------------------------------------------------------------
def ensure_default_types(conn: Connection) -> None:
    """申請種別がまだない会社に、初期セット(経費精算・購買申請・休暇申請・汎用申請)を入れる。

    承認の段で使う担当役割(経理担当・代表)がなければ空で作る。担当する社員は管理者が設定する。
    """
    if conn.execute("SELECT 1 FROM app.request_types LIMIT 1").fetchone():
        return
    groups: dict[str, UUID] = {}
    for name in {s["group"] for t in wf.DEFAULT_TYPES for s in t["steps"] if s.get("group")}:
        conn.execute(
            "INSERT INTO app.approver_groups (tenant_id, name) VALUES (app.current_tenant_id(), %s) ON CONFLICT DO NOTHING",
            (name,),
        )
        groups[name] = conn.execute("SELECT id FROM app.approver_groups WHERE name = %s", (name,)).fetchone()["id"]
    for order, t in enumerate(wf.DEFAULT_TYPES, start=1):
        row = conn.execute(
            """
            INSERT INTO app.request_types (tenant_id, code, name, description, form_schema, amount_mode, amount_label, sort_order)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING RETURNING id
            """,
            (t["code"], t["name"], t["description"], Jsonb(t["form_schema"]), t["amount_mode"], t["amount_label"], order * 10),
        ).fetchone()
        if row is None:  # 同時に別のリクエストが入れた
            continue
        for no, s in enumerate(t["steps"], start=1):
            conn.execute(
                """
                INSERT INTO app.route_steps (tenant_id, request_type_id, step_no, approver_kind, approver_group_id, min_amount)
                VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s)
                """,
                (row["id"], no, s["approver_kind"], groups.get(s.get("group", "")), s.get("min_amount")),
            )


class StepOut(BaseModel):
    step_no: int
    approver_kind: Literal["manager", "group", "user"]
    approver_group_id: UUID | None
    approver_user_id: UUID | None
    min_amount: Decimal | None
    label: str


class RequestTypeOut(BaseModel):
    id: UUID
    code: str
    name: str
    description: str | None
    form_schema: list[dict]
    amount_mode: Literal["required", "optional", "none"]
    amount_label: str
    is_active: bool
    steps: list[StepOut]


TYPE_SELECT = """
    SELECT t.id, t.code, t.name, t.description, t.form_schema, t.amount_mode, t.amount_label, t.is_active,
           coalesce((
             SELECT json_agg(json_build_object(
                      'step_no', s.step_no, 'approver_kind', s.approver_kind,
                      'approver_group_id', s.approver_group_id, 'approver_user_id', s.approver_user_id,
                      'min_amount', s.min_amount,
                      'label', CASE s.approver_kind WHEN 'manager' THEN '上長'
                                                    WHEN 'group' THEN g.name ELSE p.display_name END)
                    ORDER BY s.step_no)
             FROM app.route_steps s
             LEFT JOIN app.approver_groups g ON g.id = s.approver_group_id
             LEFT JOIN app.member_profiles p ON p.tenant_id = s.tenant_id AND p.user_id = s.approver_user_id
             WHERE s.request_type_id = t.id), '[]') AS steps
    FROM app.request_types t
"""


def _type(conn: Connection, type_id: UUID) -> dict:
    row = conn.execute(f"{TYPE_SELECT} WHERE t.id = %s", (type_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "申請種別が見つかりません")
    return row


@router.get("/api/request-types", response_model=list[RequestTypeOut])
def list_request_types(conn: TenantConn, include_inactive: bool = False):
    ensure_default_types(conn)
    return conn.execute(
        f"{TYPE_SELECT} WHERE t.is_active OR (%s AND app.current_user_is_admin()) ORDER BY t.sort_order, t.name",
        (include_inactive,),
    ).fetchall()


class StepIn(BaseModel):
    approver_kind: Literal["manager", "group", "user"]
    approver_group_id: UUID | None = None
    approver_user_id: UUID | None = None
    min_amount: Decimal | None = Field(default=None, max_digits=15, decimal_places=0)


class RequestTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    description: str | None = Field(default=None, max_length=200)
    is_active: bool = True
    steps: list[StepIn] = Field(max_length=wf.MAX_STEPS)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("種別の名前を入力してください")
        return v

    @field_validator("description")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


@router.put("/api/request-types/{type_id}", response_model=RequestTypeOut)
def update_request_type(type_id: UUID, body: RequestTypeIn, conn: AdminConn):
    """申請種別の名前・使う/使わない・承認の段を変える。進行中の申請のルートは変わらない。"""
    _type(conn, type_id)
    defs = [
        wf.StepDef(
            s.approver_kind,
            group_id=s.approver_group_id if s.approver_kind == "group" else None,
            user_id=s.approver_user_id if s.approver_kind == "user" else None,
            min_amount=s.min_amount,
        )
        for s in body.steps
    ]
    try:
        wf.check_route_definition(defs)
    except wf.WorkflowError as e:
        raise _error(e, status.HTTP_422_UNPROCESSABLE_CONTENT) from None
    if not defs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "承認の段を1つ以上設定してください")
    for d in defs:
        if d.group_id and conn.execute("SELECT 1 FROM app.approver_groups WHERE id = %s", (d.group_id,)).fetchone() is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "選んだ担当役割が見つかりません")
        if d.user_id:
            person = conn.execute(
                "SELECT is_active FROM app.member_profiles WHERE tenant_id = app.current_tenant_id() AND user_id = %s",
                (d.user_id,),
            ).fetchone()
            if person is None or not person["is_active"]:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "承認者に選んだ社員が見つからないか、使用中止です")

    try:
        with conn.transaction():
            conn.execute(
                "UPDATE app.request_types SET name = %s, description = %s, is_active = %s WHERE id = %s",
                (body.name, body.description, body.is_active, type_id),
            )
    except errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, f"申請種別「{body.name}」はすでにあります") from None
    conn.execute("DELETE FROM app.route_steps WHERE request_type_id = %s", (type_id,))
    for no, d in enumerate(defs, start=1):
        conn.execute(
            """
            INSERT INTO app.route_steps (tenant_id, request_type_id, step_no, approver_kind, approver_group_id, approver_user_id, min_amount)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s)
            """,
            (type_id, no, d.approver_kind, d.group_id, d.user_id, d.min_amount),
        )
    return _type(conn, type_id)


# ---------------------------------------------------------------------
# 承認ルートの組み立て
# ---------------------------------------------------------------------
def build_route(conn: Connection, type_row: dict, applicant: UUID, amount: Decimal | None) -> list[wf.RouteStep]:
    defs = [
        wf.StepDef(
            s["approver_kind"],
            group_id=UUID(s["approver_group_id"]) if s["approver_group_id"] else None,
            group_name=s["label"] if s["approver_kind"] == "group" else None,
            user_id=UUID(s["approver_user_id"]) if s["approver_user_id"] else None,
            min_amount=Decimal(str(s["min_amount"])) if s["min_amount"] is not None else None,
        )
        for s in type_row["steps"]
    ]
    m = conn.execute(
        """
        SELECT mp.user_id, mp.display_name, mp.is_active
        FROM app.member_profiles p
        JOIN app.member_profiles mp ON mp.tenant_id = p.tenant_id AND mp.user_id = p.manager_user_id
        WHERE p.tenant_id = app.current_tenant_id() AND p.user_id = %s
        """,
        (applicant,),
    ).fetchone()
    manager = wf.Person(m["user_id"], m["display_name"], m["is_active"]) if m else None

    group_ids = [d.group_id for d in defs if d.group_id]
    members: dict[UUID, list[wf.Person]] = {g: [] for g in group_ids}
    for r in conn.execute(
        """
        SELECT gm.group_id, p.user_id, p.display_name, p.is_active
        FROM app.approver_group_members gm
        JOIN app.member_profiles p ON p.tenant_id = gm.tenant_id AND p.user_id = gm.user_id
        WHERE gm.tenant_id = app.current_tenant_id() AND gm.group_id = ANY(%s::uuid[])
        ORDER BY p.display_name
        """,
        (group_ids,),
    ).fetchall():
        members[r["group_id"]].append(wf.Person(r["user_id"], r["display_name"], r["is_active"]))

    user_ids = [d.user_id for d in defs if d.user_id]
    people = {
        r["user_id"]: wf.Person(r["user_id"], r["display_name"], r["is_active"])
        for r in conn.execute(
            "SELECT user_id, display_name, is_active FROM app.member_profiles "
            "WHERE tenant_id = app.current_tenant_id() AND user_id = ANY(%s::uuid[])",
            (user_ids,),
        ).fetchall()
    }
    return wf.build_route(defs, applicant=applicant, amount=amount, manager=manager, group_members=members, people=people)


class Person(BaseModel):
    user_id: UUID
    display_name: str


def _names(conn: Connection, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT user_id, display_name FROM app.member_profiles WHERE tenant_id = app.current_tenant_id() AND user_id = ANY(%s::uuid[])",
        (list(ids),),
    ).fetchall()
    return {r["user_id"]: r["display_name"] for r in rows}


class RouteStepOut(BaseModel):
    step_no: int
    label: str
    approver_kind: str
    status: str  # waiting / pending / approved / returned / rejected / skipped / cancelled
    skip_reason: str | None = None
    approvers: list[Person]
    acted_by: Person | None = None
    acted_at: datetime | None = None
    comment: str | None = None


class RoutePreviewIn(BaseModel):
    request_type_id: UUID
    amount: Decimal | None = Field(default=None, max_digits=15, decimal_places=0)


class RoutePreviewOut(BaseModel):
    steps: list[RouteStepOut]
    auto_approve: bool  # すべての段が省略され、申請と同時に承認済になる
    error: str | None  # 申請できない理由(担当役割に社員がいないなど)


@router.post("/api/requests/route-preview", response_model=RoutePreviewOut)
def route_preview(body: RoutePreviewIn, conn: TenantConn):
    """申請するとどの承認ルートになるか(自分が申請者の場合)。金額の条件も反映する。"""
    me, _ = _me(conn)
    type_row = _type(conn, body.request_type_id)
    try:
        route = build_route(conn, type_row, me, body.amount)
    except wf.WorkflowError as e:
        return RoutePreviewOut(steps=[], auto_approve=False, error=str(e))
    names = _names(conn, {u for s in route for u in s.candidates})
    steps = [
        RouteStepOut(
            step_no=s.step_no, label=s.label, approver_kind=s.approver_kind, status=s.status, skip_reason=s.skip_reason,
            approvers=[Person(user_id=u, display_name=names.get(u, "")) for u in s.candidates],
        )
        for s in route
    ]
    return RoutePreviewOut(steps=steps, auto_approve=all(s.status == "skipped" for s in route), error=None)


# ---------------------------------------------------------------------
# 申請の一覧・詳細
# ---------------------------------------------------------------------
class RequestListItem(BaseModel):
    id: UUID
    request_number: str | None
    type_name: str
    type_code: str
    title: str
    applicant_id: UUID
    applicant_name: str
    amount: Decimal | None
    status: Literal["draft", "pending", "returned", "approved", "rejected", "withdrawn"]
    current_step_label: str | None
    submitted_at: datetime | None
    decided_at: datetime | None
    updated_at: datetime
    is_my_turn: bool


LIST_SELECT = """
    SELECT r.id, r.request_number, t.name AS type_name, t.code AS type_code, r.title, r.applicant_id,
           a.display_name AS applicant_name, r.amount, r.status, cs.label AS current_step_label,
           r.submitted_at, r.decided_at, r.updated_at,
           coalesce(cs.status = 'pending' AND app.current_user_id() = ANY (cs.candidate_user_ids), false) AS is_my_turn
    FROM app.requests r
    JOIN app.request_types t ON t.id = r.request_type_id
    JOIN app.member_profiles a ON a.tenant_id = r.tenant_id AND a.user_id = r.applicant_id
    LEFT JOIN app.request_steps cs ON cs.request_id = r.id AND cs.round = r.round AND cs.step_no = r.current_step
"""


@router.get("/api/requests", response_model=list[RequestListItem])
def list_requests(
    conn: TenantConn,
    box: Literal["todo", "mine", "all"] = "mine",
    status_filter: Literal["draft", "pending", "returned", "approved", "rejected", "withdrawn"] | None = None,
):
    """todo: 自分の承認待ち(古い順) / mine: 自分の申請 / all: すべて(管理者)。"""
    me, admin = _me(conn)
    where, order = [], "r.updated_at DESC"
    if box == "todo":
        where.append("r.status = 'pending' AND cs.status = 'pending' AND app.current_user_id() = ANY (cs.candidate_user_ids)")
        order = "r.submitted_at, r.request_number"
    elif box == "mine":
        where.append("r.applicant_id = app.current_user_id()")
    elif not admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "すべての申請を見られるのは管理者だけです")
    else:
        where.append("r.status <> 'draft'")  # ほかの人の下書きは出さない
    params: list = []
    if status_filter:
        where.append("r.status = %s")
        params.append(status_filter)
    return conn.execute(f"{LIST_SELECT} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 300", params).fetchall()


class SummaryOut(BaseModel):
    todo: int  # 自分の承認待ち
    returned: int  # 差し戻された自分の申請


@router.get("/api/requests/summary", response_model=SummaryOut)
def summary(conn: TenantConn):
    """メニューに出す件数。"""
    return conn.execute(
        f"""
        SELECT
          (SELECT count(*) FROM ({LIST_SELECT}
             WHERE r.status = 'pending' AND cs.status = 'pending' AND app.current_user_id() = ANY (cs.candidate_user_ids)) x) AS todo,
          (SELECT count(*) FROM app.requests WHERE applicant_id = app.current_user_id() AND status = 'returned') AS returned
        """
    ).fetchone()


class EventOut(BaseModel):
    id: int
    action: str
    round: int | None
    step_no: int | None
    step_label: str | None
    actor: Person | None
    comment: str | None
    created_at: datetime


class AttachmentOut(BaseModel):
    id: UUID
    file_name: str
    content_type: str
    size_bytes: int
    uploaded_by_name: str | None
    created_at: datetime


class Permissions(BaseModel):
    can_edit: bool  # 内容を直す(下書き・差戻し)
    can_submit: bool
    can_delete: bool  # 下書きの削除
    can_withdraw: bool
    can_act: bool  # 承認・差戻し・却下(自分の番)
    can_comment: bool


class RequestDetail(RequestListItem):
    request_type_id: UUID
    form_schema: list[dict]
    form_data: dict
    amount_mode: str
    amount_label: str
    round: int
    steps: list[RouteStepOut]
    events: list[EventOut]
    attachments: list[AttachmentOut]
    permissions: Permissions


def _row(conn: Connection, request_id: UUID, lock: bool = False) -> dict:
    """申請を読む。見る権限のない申請は RLS で見えないので、見つからない扱い(404)になる。"""
    row = conn.execute(
        f"""
        SELECT r.*, t.name AS type_name, t.code AS type_code, t.form_schema, t.amount_mode, t.amount_label, t.is_active AS type_active
        FROM app.requests r JOIN app.request_types t ON t.id = r.request_type_id
        WHERE r.id = %s {"FOR UPDATE OF r" if lock else ""}
        """,
        (request_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "申請が見つかりません")
    me, admin = _me(conn)
    in_route = conn.execute(
        "SELECT 1 FROM app.request_steps WHERE request_id = %s AND %s = ANY (candidate_user_ids) LIMIT 1",
        (request_id, me),
    ).fetchone()
    # RLS と同じ範囲を API でも確かめる(どちらかの設定を誤っても漏れないように)
    if not (row["applicant_id"] == me or admin or in_route):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "申請が見つかりません")
    return row


def _steps(conn: Connection, request_id: UUID, round_no: int) -> list[RouteStepOut]:
    rows = conn.execute(
        "SELECT * FROM app.request_steps WHERE request_id = %s AND round = %s ORDER BY step_no", (request_id, round_no)
    ).fetchall()
    names = _names(conn, {u for r in rows for u in r["candidate_user_ids"]} | {r["acted_by"] for r in rows if r["acted_by"]})
    return [
        RouteStepOut(
            step_no=r["step_no"], label=r["label"], approver_kind=r["approver_kind"], status=r["status"],
            skip_reason=r["skip_reason"],
            approvers=[Person(user_id=u, display_name=names.get(u, "")) for u in r["candidate_user_ids"]],
            acted_by=Person(user_id=r["acted_by"], display_name=names.get(r["acted_by"], "")) if r["acted_by"] else None,
            acted_at=r["acted_at"], comment=r["comment"],
        )
        for r in rows
    ]


def _detail(conn: Connection, request_id: UUID) -> RequestDetail:
    row = _row(conn, request_id)
    me, _ = _me(conn)
    item = conn.execute(f"{LIST_SELECT} WHERE r.id = %s", (request_id,)).fetchone()
    events = conn.execute(
        """
        SELECT e.id, e.action, e.round, e.step_no, s.label AS step_label, e.actor_id, p.display_name AS actor_name,
               e.comment, e.created_at
        FROM app.request_events e
        LEFT JOIN app.request_steps s ON s.request_id = e.request_id AND s.round = e.round AND s.step_no = e.step_no
        LEFT JOIN app.member_profiles p ON p.tenant_id = e.tenant_id AND p.user_id = e.actor_id
        WHERE e.request_id = %s ORDER BY e.id
        """,
        (request_id,),
    ).fetchall()
    attachments = conn.execute(
        """
        SELECT a.id, a.file_name, a.content_type, a.size_bytes, p.display_name AS uploaded_by_name, a.created_at
        FROM app.request_attachments a
        LEFT JOIN app.member_profiles p ON p.tenant_id = a.tenant_id AND p.user_id = a.uploaded_by
        WHERE a.request_id = %s ORDER BY a.created_at
        """,
        (request_id,),
    ).fetchall()
    mine = row["applicant_id"] == me
    st = row["status"]
    return RequestDetail(
        **item,
        request_type_id=row["request_type_id"],
        form_schema=row["form_schema"],
        form_data=row["form_data"],
        amount_mode=row["amount_mode"],
        amount_label=row["amount_label"],
        round=row["round"],
        steps=_steps(conn, request_id, row["round"]) if row["round"] else [],
        events=[
            EventOut(
                **{k: e[k] for k in ("id", "action", "round", "step_no", "step_label", "comment", "created_at")},
                actor=Person(user_id=e["actor_id"], display_name=e["actor_name"] or "") if e["actor_id"] else None,
            )
            for e in events
        ],
        attachments=attachments,
        permissions=Permissions(
            can_edit=mine and wf.can(st, "edit"),
            can_submit=mine and wf.can(st, "submit"),
            can_delete=mine and wf.can(st, "delete"),
            can_withdraw=mine and wf.can(st, "withdraw"),
            can_act=item["is_my_turn"],
            can_comment=st != "draft",
        ),
    )


@router.get("/api/requests/{request_id}", response_model=RequestDetail)
def get_request(request_id: UUID, conn: TenantConn):
    return _detail(conn, request_id)


# ---------------------------------------------------------------------
# 下書きの作成・更新・削除
# ---------------------------------------------------------------------
class RequestIn(BaseModel):
    request_type_id: UUID
    title: str = Field(min_length=1, max_length=100)
    form_data: dict = Field(default_factory=dict)
    amount: Decimal | None = Field(default=None, max_digits=15, decimal_places=0)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("件名を入力してください")
        return v


def _clean(type_row: dict, body: RequestIn, *, partial: bool) -> tuple[dict, Decimal | None]:
    try:
        data = wf.clean_form(type_row["form_schema"], body.form_data, partial=partial)
        amount = wf.check_amount(type_row["amount_mode"], body.amount, type_row["amount_label"], partial=partial)
    except wf.WorkflowError as e:
        raise _error(e, status.HTTP_422_UNPROCESSABLE_CONTENT) from None
    return data, amount


@router.post("/api/requests", response_model=RequestDetail, status_code=status.HTTP_201_CREATED)
def create_request(body: RequestIn, conn: TenantConn):
    """下書きを作る。申請は /submit で行う。"""
    type_row = _type(conn, body.request_type_id)
    if not type_row["is_active"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "この申請種別はいま使われていません")
    data, amount = _clean(type_row, body, partial=True)
    row = conn.execute(
        """
        INSERT INTO app.requests (tenant_id, request_type_id, applicant_id, title, form_data, amount)
        VALUES (app.current_tenant_id(), %s, app.current_user_id(), %s, %s, %s) RETURNING id
        """,
        (body.request_type_id, body.title, Jsonb(data), amount),
    ).fetchone()
    return _detail(conn, row["id"])


def _own(conn: Connection, request_id: UUID, action: str, lock: bool = True) -> dict:
    row = _row(conn, request_id, lock=lock)
    me, _ = _me(conn)
    if row["applicant_id"] != me:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "この操作は申請者だけが行えます")
    try:
        wf.check_action(row["status"], action)
    except wf.WorkflowError as e:
        raise _error(e) from None
    return row


@router.put("/api/requests/{request_id}", response_model=RequestDetail)
def update_request(request_id: UUID, body: RequestIn, conn: TenantConn):
    """下書き・差し戻された申請を直す。差し戻された申請は種別を変えられない。"""
    row = _own(conn, request_id, "edit")
    if row["status"] == "returned" and body.request_type_id != row["request_type_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "差し戻された申請は種別を変えられません。取り下げて新しく申請してください")
    type_row = _type(conn, body.request_type_id)
    data, amount = _clean(type_row, body, partial=True)
    conn.execute(
        "UPDATE app.requests SET request_type_id = %s, title = %s, form_data = %s, amount = %s WHERE id = %s",
        (body.request_type_id, body.title, Jsonb(data), amount, request_id),
    )
    return _detail(conn, request_id)


@router.delete("/api/requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request(request_id: UUID, conn: TenantConn):
    _own(conn, request_id, "delete")
    paths = [r["storage_path"] for r in conn.execute(
        "SELECT storage_path FROM app.request_attachments WHERE request_id = %s", (request_id,)
    ).fetchall()]
    conn.execute("DELETE FROM app.requests WHERE id = %s", (request_id,))
    for p in paths:
        storage.delete(p)


# ---------------------------------------------------------------------
# 申請・承認・差戻し・却下・取下げ・コメント
# ---------------------------------------------------------------------
def _event(conn: Connection, request_id: UUID, action: str, round_no: int | None = None, step_no: int | None = None,
           comment: str | None = None, actor: bool = True) -> None:
    conn.execute(
        """
        INSERT INTO app.request_events (tenant_id, request_id, round, step_no, action, actor_id, comment)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, CASE WHEN %s THEN app.current_user_id() END, %s)
        """,
        (request_id, round_no, step_no, action, actor, comment),
    )


def _finish(conn: Connection, request_id: UUID, new_status: str, round_no: int) -> None:
    """申請を終わらせる(承認済・却下・取下げ)。残っている段は取り消し扱いにする。"""
    conn.execute(
        "UPDATE app.request_steps SET status = 'cancelled' WHERE request_id = %s AND round = %s AND status IN ('waiting', 'pending')",
        (request_id, round_no),
    )
    conn.execute(
        "UPDATE app.requests SET status = %s, current_step = NULL, decided_at = now() WHERE id = %s",
        (new_status, request_id),
    )


@router.post("/api/requests/{request_id}/submit", response_model=RequestDetail)
def submit_request(request_id: UUID, conn: TenantConn):
    """申請する(差し戻された申請の再申請も同じ)。承認ルートはこの時点の設定で作り直し、1段目からやり直す。"""
    row = _own(conn, request_id, "submit")
    type_row = _type(conn, row["request_type_id"])
    if not type_row["is_active"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "この申請種別はいま使われていません。管理者に確認してください")
    body = RequestIn(request_type_id=row["request_type_id"], title=row["title"], form_data=row["form_data"],
                     amount=row["amount"])
    _clean(type_row, body, partial=False)
    try:
        route = build_route(conn, type_row, row["applicant_id"], row["amount"])
    except wf.WorkflowError as e:
        raise _error(e, status.HTTP_422_UNPROCESSABLE_CONTENT) from None

    round_no = row["round"] + 1
    number = row["request_number"] or conn.execute(
        "SELECT app.next_document_number('request', %s) AS n", (today(),)
    ).fetchone()["n"]
    first = next((s for s in route if s.status == "waiting"), None)
    conn.execute(
        """
        UPDATE app.requests
        SET request_number = %s, round = %s, status = %s, current_step = %s, submitted_at = now(),
            decided_at = CASE WHEN %s THEN NULL ELSE now() END
        WHERE id = %s
        """,
        (number, round_no, "pending" if first else "approved", first.step_no if first else None, first is not None, request_id),
    )
    _event(conn, request_id, "resubmit" if round_no > 1 else "submit", round_no)
    for s in route:
        conn.execute(
            """
            INSERT INTO app.request_steps (tenant_id, request_id, round, step_no, approver_kind, label, candidate_user_ids, status, skip_reason)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (request_id, round_no, s.step_no, s.approver_kind, s.label, s.candidates,
             "pending" if s is first else s.status, s.skip_reason),
        )
        if s.status == "skipped":
            _event(conn, request_id, "skip", round_no, s.step_no, s.skip_reason, actor=False)
    if first is None:  # すべての段が省略された(代表本人の申請など)。申請と同時に承認済
        _event(conn, request_id, "complete", round_no, actor=False)
    return _detail(conn, request_id)


class ActionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)


def _my_step(conn: Connection, request_id: UUID, action: str) -> tuple[dict, dict]:
    row = _row(conn, request_id, lock=True)
    try:
        wf.check_action(row["status"], action)
    except wf.WorkflowError as e:
        raise _error(e) from None
    step = conn.execute(
        "SELECT * FROM app.request_steps WHERE request_id = %s AND round = %s AND step_no = %s",
        (request_id, row["round"], row["current_step"]),
    ).fetchone()
    me, _ = _me(conn)
    if step is None or step["status"] != "pending" or me not in step["candidate_user_ids"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "この申請はあなたの承認待ちではありません")
    return row, step


def _record(conn: Connection, step: dict, new_status: str, comment: str | None) -> None:
    conn.execute(
        "UPDATE app.request_steps SET status = %s, acted_by = app.current_user_id(), acted_at = now(), comment = %s WHERE id = %s",
        (new_status, comment, step["id"]),
    )


@router.post("/api/requests/{request_id}/approve", response_model=RequestDetail)
def approve_request(request_id: UUID, body: ActionIn, conn: TenantConn):
    row, step = _my_step(conn, request_id, "approve")
    comment = wf.require_comment("approve", body.comment)
    _record(conn, step, "approved", comment)
    _event(conn, request_id, "approve", row["round"], step["step_no"], comment)
    nxt = conn.execute(
        "SELECT * FROM app.request_steps WHERE request_id = %s AND round = %s AND status = 'waiting' ORDER BY step_no LIMIT 1",
        (request_id, row["round"]),
    ).fetchone()
    if nxt:
        conn.execute("UPDATE app.request_steps SET status = 'pending' WHERE id = %s", (nxt["id"],))
        conn.execute("UPDATE app.requests SET current_step = %s WHERE id = %s", (nxt["step_no"], request_id))
    else:
        _finish(conn, request_id, "approved", row["round"])
        _event(conn, request_id, "complete", row["round"], actor=False)
    return _detail(conn, request_id)


def _comment_or_422(action: str, comment: str | None) -> str | None:
    try:
        return wf.require_comment(action, comment)
    except wf.WorkflowError as e:
        raise _error(e, status.HTTP_422_UNPROCESSABLE_CONTENT) from None


@router.post("/api/requests/{request_id}/return", response_model=RequestDetail)
def return_request(request_id: UUID, body: ActionIn, conn: TenantConn):
    """差し戻す(理由は必須)。申請者が直して再申請すると、1段目からやり直す。"""
    comment = _comment_or_422("return", body.comment)
    row, step = _my_step(conn, request_id, "return")
    _record(conn, step, "returned", comment)
    conn.execute(
        "UPDATE app.request_steps SET status = 'cancelled' WHERE request_id = %s AND round = %s AND status = 'waiting'",
        (request_id, row["round"]),
    )
    conn.execute("UPDATE app.requests SET status = 'returned', current_step = NULL WHERE id = %s", (request_id,))
    _event(conn, request_id, "return", row["round"], step["step_no"], comment)
    return _detail(conn, request_id)


@router.post("/api/requests/{request_id}/reject", response_model=RequestDetail)
def reject_request(request_id: UUID, body: ActionIn, conn: TenantConn):
    """却下する(理由は必須)。却下は終了で、再申請できない。"""
    comment = _comment_or_422("reject", body.comment)
    row, step = _my_step(conn, request_id, "reject")
    _record(conn, step, "rejected", comment)
    _finish(conn, request_id, "rejected", row["round"])
    _event(conn, request_id, "reject", row["round"], step["step_no"], comment)
    return _detail(conn, request_id)


@router.post("/api/requests/{request_id}/withdraw", response_model=RequestDetail)
def withdraw_request(request_id: UUID, body: ActionIn, conn: TenantConn):
    """取り下げる(承認が終わる前なら、申請者がいつでも行える)。"""
    row = _own(conn, request_id, "withdraw")
    comment = _comment_or_422("withdraw", body.comment)
    _finish(conn, request_id, "withdrawn", row["round"])
    _event(conn, request_id, "withdraw", row["round"], None, comment)
    return _detail(conn, request_id)


@router.post("/api/requests/{request_id}/comments", response_model=RequestDetail)
def comment_request(request_id: UUID, body: ActionIn, conn: TenantConn):
    """コメントを残す(申請した後なら、申請を見られる人は誰でも)。"""
    row = _row(conn, request_id)
    if row["status"] == "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "下書きにはコメントできません")
    comment = _comment_or_422("comment", body.comment)
    if comment is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "コメントを入力してください")
    _event(conn, request_id, "comment", row["round"], None, comment)
    return _detail(conn, request_id)


# ---------------------------------------------------------------------
# 添付ファイル
# ---------------------------------------------------------------------
class AttachmentIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=200)
    content_type: str | None = Field(default=None, max_length=100)
    content_base64: str = Field(min_length=1)


@router.post("/api/requests/{request_id}/attachments", response_model=RequestDetail, status_code=status.HTTP_201_CREATED)
def add_attachment(request_id: UUID, body: AttachmentIn, conn: TenantConn):
    """添付を追加する(下書き・差し戻された申請だけ。承認が始まった後は内容を固定する)。"""
    _own(conn, request_id, "edit")
    count = conn.execute("SELECT count(*) AS n FROM app.request_attachments WHERE request_id = %s", (request_id,)).fetchone()["n"]
    if count >= MAX_ATTACHMENTS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"添付は{MAX_ATTACHMENTS}個までです")
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ファイルを読み込めませんでした") from None
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "空のファイルは添付できません")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ファイルが大きすぎます(10MBまで)")
    name = body.file_name.replace("/", "_").replace("\\", "_").strip() or "file"
    content_type = (body.content_type or mimetypes.guess_type(name)[0] or "").lower()
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "添付できるのは画像(JPEG・PNGなど)とPDFだけです")

    tenant = conn.execute("SELECT app.current_tenant_id() AS t").fetchone()["t"]
    ext = mimetypes.guess_extension(content_type) or ""
    key = f"requests/{tenant}/{request_id}/{uuid.uuid4()}{ext}"
    sha = storage.save(key, data)
    conn.execute(
        """
        INSERT INTO app.request_attachments (tenant_id, request_id, storage_path, file_name, content_type, size_bytes, sha256, uploaded_by)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, app.current_user_id())
        """,
        (request_id, key, name, content_type, len(data), sha),
    )
    return _detail(conn, request_id)


def _attachment(conn: Connection, request_id: UUID, attachment_id: UUID) -> dict:
    _row(conn, request_id)
    att = conn.execute(
        "SELECT * FROM app.request_attachments WHERE id = %s AND request_id = %s", (attachment_id, request_id)
    ).fetchone()
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "添付が見つかりません")
    return att


@router.get("/api/requests/{request_id}/attachments/{attachment_id}")
def get_attachment(request_id: UUID, attachment_id: UUID, conn: TenantConn):
    att = _attachment(conn, request_id, attachment_id)
    try:
        data = storage.read(att["storage_path"])
    except (OSError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "添付ファイルが見つかりません") from None
    return Response(
        content=data,
        media_type=att["content_type"],
        headers={
            "Content-Disposition": f"inline; filename=\"attachment\"; filename*=UTF-8''{quote(att['file_name'])}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/api/requests/{request_id}/attachments/{attachment_id}", response_model=RequestDetail)
def delete_attachment(request_id: UUID, attachment_id: UUID, conn: TenantConn):
    _own(conn, request_id, "edit")
    att = _attachment(conn, request_id, attachment_id)
    conn.execute("DELETE FROM app.request_attachments WHERE id = %s", (attachment_id,))
    storage.delete(att["storage_path"])
    return _detail(conn, request_id)
