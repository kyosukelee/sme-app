"""注文書の取り込み(AI で読み取り、人が確認して受注にする)の API。

流れ: アップロード(処理待ち)→ 裏で読み取り(処理中)→ 要確認 → 確定(受注を作る)/ 破棄
  ・使えるのは、サーバーで AI の提供元が設定されていて(AI_PROVIDER)、会社でオンにしているときだけ
  ・オン・オフの切り替えは管理者だけ。初期値はオフ(オンにすると PDF の内容を AI のサービスに送るため)
  ・取り込みの記録と PDF は消さない(破棄しても残す)
"""

import base64
import binascii
import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError, field_validator

from app import storage
from app.ai_import import review
from app.ai_import.providers import is_available, provider_label
from app.ai_import.runner import fail_stale, process_import
from app import activity
from app.auth import AdminConn, TenantConn
from app.documents import pdf_response
from app.domain import import_checks as ic
from app.domain.names import normalize_company
from app.routers.orders import OrderIn, create_order_record
from app.schemas import OrderLineIn, TaxCategory

router = APIRouter(prefix="/api/imports", tags=["imports"])

MAX_BYTES = 10 * 1024 * 1024
ImportStatus = Literal["queued", "processing", "review", "failed", "confirmed", "discarded"]
ListFilter = Literal["all", "open", "confirmed", "discarded"]


# ---------------------------------------------------------------------
# 入出力の型
# ---------------------------------------------------------------------
class ImportSettings(BaseModel):
    available: bool        # サーバーで AI の提供元が設定されているか
    enabled: bool          # この会社でオンにしているか
    provider: str          # 画面に出す提供元の名前
    can_edit: bool


class SettingsIn(BaseModel):
    enabled: bool


class UploadIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1)


class DraftLine(BaseModel):
    description: str = Field(default="", max_length=500)
    quantity: str = Field(default="", max_length=30)
    unit: str = Field(default="", max_length=20)
    unit_price: str = Field(default="", max_length=30)
    tax_category: TaxCategory = "standard"
    item_id: UUID | None = None
    # 注文書に書かれていた値(照合用。画面からはそのまま送り返す)
    item_code: str | None = Field(default=None, max_length=100)
    doc_amount: str | None = Field(default=None, max_length=30)
    doc_tax_rate: Literal["10", "8", "0"] | None = None
    page: int | None = None


class DraftIn(BaseModel):
    customer_id: UUID | None = None
    customer_order_ref: str = Field(default="", max_length=100)
    order_date: date | None = None
    delivery_due_date: date | None = None
    title: str = Field(default="", max_length=300)
    remarks: str = Field(default="", max_length=3000)
    lines: list[DraftLine] = Field(default_factory=list, max_length=150)

    @field_validator("customer_id", "order_date", "delivery_due_date", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        # 画面の下書きでは、未入力を空文字で持つ
        return None if value == "" else value


class ConfirmIn(BaseModel):
    draft: DraftIn
    acknowledged: list[str] = Field(default_factory=list, max_length=300)  # 確認した警告の id


class RetryIn(BaseModel):
    as_order: bool = False  # 種類の判定を飛ばして、注文書として読み直す


class DiscardIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class ImportListItem(BaseModel):
    id: UUID
    status: ImportStatus
    file_name: str
    created_at: datetime
    updated_at: datetime
    issuer_name: str | None
    doc_total: str | None
    error_count: int         # 直すまで確定できない項目(取引先の選択待ちは含めない)
    warning_count: int
    needs_customer: bool     # 取引先をまだ選んでいない
    error_message: str | None
    order_id: UUID | None
    order_number: str | None


class ImportDetail(ImportListItem):
    page_count: int | None
    provider: str | None
    model: str | None
    prompt_version: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    classification: dict | None
    extracted: dict | None        # AI の読み取り結果(ExtractedOrder)
    draft: dict | None
    checks: list[dict]
    customer_candidates: list[dict]
    discard_reason: str | None
    confirmed_at: datetime | None


# ---------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------
COLUMNS = """
    d.id, d.status, d.file_name, d.created_at, d.updated_at, d.error_message, d.page_count, d.provider, d.model,
    d.prompt_version, d.duration_ms, d.input_tokens, d.output_tokens, d.extracted, d.draft, d.checks,
    d.discard_reason, d.confirmed_at, d.storage_path, o.id AS order_id, o.order_number
"""
FROM = "FROM app.document_imports d LEFT JOIN app.orders o ON o.source_import_id = d.id"


def _settings(conn: Connection) -> dict:
    row = conn.execute("SELECT ai_import_enabled FROM app.tenant_settings").fetchone()
    role = conn.execute(
        "SELECT role FROM app.memberships WHERE tenant_id = app.current_tenant_id() AND user_id = app.current_user_id()"
    ).fetchone()
    return {
        "available": is_available(),
        "enabled": bool(row and row["ai_import_enabled"]),
        "provider": provider_label(),
        "can_edit": bool(role and role["role"] == "admin"),
    }


def _require_enabled(conn: Connection) -> None:
    s = _settings(conn)
    if not s["available"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "このサーバーでは AI による取り込みが設定されていません(AI_PROVIDER)")
    if not s["enabled"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AI による注文書の取り込みはオフになっています。自社情報の画面でオンにしてください")


def _summary(row: dict) -> dict:
    order = (row.get("extracted") or {}).get("order") or {}
    checks = row.get("checks") or []
    return {
        **row,
        "issuer_name": order.get("issuer_name"),
        "doc_total": ic.to_number_text(order.get("total")) or None,
        "error_count": sum(c["level"] == "error" and c["id"] != "customer_missing" for c in checks),
        "needs_customer": any(c["id"] == "customer_missing" for c in checks),
        "warning_count": sum(c["level"] == "warning" for c in checks),
    }


def _row(conn: Connection, import_id: UUID, *, for_update: bool = False) -> dict:
    lock = "FOR UPDATE OF d" if for_update else ""
    row = conn.execute(f"SELECT {COLUMNS} {FROM} WHERE d.id = %s {lock}", (import_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "取り込みが見つかりません")
    return row


def _detail(conn: Connection, import_id: UUID) -> dict:
    row = _summary(_row(conn, import_id))
    extracted = row.get("extracted") or {}
    order = extracted.get("order")
    candidates = review.customer_candidates(conn, order) if order and row["status"] == "review" else []
    return {
        **row,
        "classification": extracted.get("classification"),
        "extracted": order,
        "checks": row.get("checks") or [],
        "customer_candidates": candidates,
    }


def _draft_dict(body: DraftIn) -> dict:
    data = json.loads(body.model_dump_json())
    data["order_date"] = data["order_date"] or ""
    data["delivery_due_date"] = data["delivery_due_date"] or ""
    data["customer_id"] = data["customer_id"] or None
    for line in data["lines"]:
        line["item_id"] = line["item_id"] or None
    return data


def _current(conn: Connection) -> tuple[UUID, UUID]:
    row = conn.execute("SELECT app.current_tenant_id() AS t, app.current_user_id() AS u").fetchone()
    return row["t"], row["u"]


# ---------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------
@router.get("/settings", response_model=ImportSettings)
def get_settings(conn: TenantConn):
    return _settings(conn)


@router.put("/settings", response_model=ImportSettings)
def update_settings(body: SettingsIn, conn: AdminConn):
    if body.enabled and not is_available():
        raise HTTPException(status.HTTP_409_CONFLICT, "このサーバーでは AI の提供元が設定されていないため、オンにできません")
    conn.execute("UPDATE app.tenant_settings SET ai_import_enabled = %s", (body.enabled,))
    return _settings(conn)


# ---------------------------------------------------------------------
# 取り込み
# ---------------------------------------------------------------------
@router.get("", response_model=list[ImportListItem])
def list_imports(conn: TenantConn, filter: ListFilter = "all"):
    fail_stale(conn)
    rows = conn.execute(
        f"""
        SELECT {COLUMNS} {FROM}
        WHERE %(f)s = 'all'
           OR (%(f)s = 'open' AND d.status IN ('queued', 'processing', 'review', 'failed'))
           OR d.status = %(f)s
        ORDER BY d.created_at DESC
        LIMIT 200
        """,
        {"f": filter},
    ).fetchall()
    return [_summary(r) for r in rows]


@router.post("", response_model=ImportDetail, status_code=status.HTTP_201_CREATED)
def upload(body: UploadIn, conn: TenantConn, background: BackgroundTasks):
    """PDF を受け取って保存し、裏で読み取りを始める。"""
    _require_enabled(conn)
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ファイルを読み込めませんでした") from None
    if len(data) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ファイルが大きすぎます(10MBまで)")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "PDFファイルを選んでください")

    sha = hashlib.sha256(data).hexdigest()
    existing = conn.execute(
        "SELECT created_at FROM app.document_imports WHERE sha256 = %s AND status <> 'discarded'", (sha,)
    ).fetchone()
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"このPDFは {existing['created_at']:%Y/%m/%d %H:%M} に取り込み済みです。一覧から開いてください",
        )

    tenant_id, user_id = _current(conn)
    import_id = uuid.uuid4()
    name = body.file_name.replace("/", "_").replace("\\", "_").strip() or "注文書.pdf"
    key = f"imports/{tenant_id}/{import_id}.pdf"
    storage.save(key, data)
    conn.execute(
        """
        INSERT INTO app.document_imports
          (id, tenant_id, file_name, content_type, size_bytes, sha256, storage_path, created_by)
        VALUES (%s, app.current_tenant_id(), %s, 'application/pdf', %s, %s, %s, app.current_user_id())
        """,
        (import_id, name, len(data), sha, key),
    )
    background.add_task(process_import, import_id, tenant_id, user_id)
    return _detail(conn, import_id)


@router.get("/{import_id}", response_model=ImportDetail)
def get_import(import_id: UUID, conn: TenantConn):
    fail_stale(conn)
    return _detail(conn, import_id)


@router.get("/{import_id}/pdf")
def get_pdf(import_id: UUID, conn: TenantConn) -> Response:
    row = _row(conn, import_id)
    activity.record(conn, "open_import_pdf", f"取り込んだ注文書「{row['file_name']}」", "document_import", import_id)
    return pdf_response(storage.read(row["storage_path"]), row["file_name"])


@router.put("/{import_id}/draft", response_model=ImportDetail)
def save_draft(import_id: UUID, body: DraftIn, conn: TenantConn):
    """確認画面の修正を保存し、検証し直す。"""
    row = _row(conn, import_id, for_update=True)
    if row["status"] != "review":
        raise HTTPException(status.HTTP_409_CONFLICT, "要確認の取り込みだけ修正できます")
    draft = _draft_dict(body)
    checks = review.evaluate(conn, row["extracted"]["order"], draft)
    conn.execute(
        "UPDATE app.document_imports SET draft = %s, checks = %s WHERE id = %s", (Jsonb(draft), Jsonb(checks), import_id)
    )
    return _detail(conn, import_id)


@router.post("/{import_id}/retry", response_model=ImportDetail)
def retry(import_id: UUID, body: RetryIn, conn: TenantConn, background: BackgroundTasks):
    """失敗した取り込みを読み直す。as_order なら種類の判定を飛ばして注文書として読む。"""
    _require_enabled(conn)
    row = _row(conn, import_id, for_update=True)
    if row["status"] != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "やり直せるのは失敗した取り込みだけです")
    conn.execute("UPDATE app.document_imports SET status = 'queued', error_message = NULL WHERE id = %s", (import_id,))
    tenant_id, user_id = _current(conn)
    background.add_task(process_import, import_id, tenant_id, user_id, body.as_order)
    return _detail(conn, import_id)


@router.post("/{import_id}/discard", response_model=ImportDetail)
def discard(import_id: UUID, body: DiscardIn, conn: TenantConn):
    row = _row(conn, import_id, for_update=True)
    if row["status"] not in ("review", "failed", "queued"):
        raise HTTPException(status.HTTP_409_CONFLICT, "この取り込みは破棄できません")
    conn.execute(
        "UPDATE app.document_imports SET status = 'discarded', discard_reason = %s WHERE id = %s",
        ((body.reason or "").strip() or None, import_id),
    )
    return _detail(conn, import_id)


@router.post("/{import_id}/confirm", response_model=ImportDetail)
def confirm(import_id: UUID, body: ConfirmIn, conn: TenantConn):
    """確認した内容で受注を作る。エラーが残っている、または確認していない警告があれば確定しない。"""
    row = _row(conn, import_id, for_update=True)
    if row["status"] != "review":
        raise HTTPException(status.HTTP_409_CONFLICT, "要確認の取り込みだけ確定できます")
    order = row["extracted"]["order"]
    draft = _draft_dict(body.draft)
    checks = review.evaluate(conn, order, draft)

    errors = [c for c in checks if c["level"] == "error"]
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"確定できません: {errors[0]['message']}")
    pending = [c for c in checks if c["level"] == "warning" and c["id"] not in set(body.acknowledged)]
    if pending:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"確認していない警告があります: {pending[0]['message']}"
        )

    try:
        order_in = OrderIn(
            customer_id=draft["customer_id"],
            title=draft["title"] or None,
            remarks=draft["remarks"] or None,
            order_date=draft["order_date"] or None,
            delivery_due_date=draft["delivery_due_date"] or None,
            customer_order_ref=(draft["customer_order_ref"] or "").strip() or None,
            lines=[
                OrderLineIn(
                    description=l["description"].strip(),
                    quantity=Decimal(ic.to_number_text(l["quantity"])),
                    unit=(l["unit"] or "").strip() or None,
                    unit_price=Decimal(ic.to_number_text(l["unit_price"])),
                    tax_category=l["tax_category"],
                )
                for l in draft["lines"]
                if not ic.is_blank_line(l)
            ],
        )
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "受注として登録できない値があります。明細を確認してください") from e

    create_order_record(conn, order_in, source_import_id=import_id)
    _remember_document_name(conn, draft["customer_id"], order.get("issuer_name"))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO app.document_import_corrections (tenant_id, import_id, field, ai_value, final_value)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s)
            """,
            [(import_id, f, a, b) for f, a, b in ic.corrections(order, draft)],
        )
    conn.execute(
        """
        UPDATE app.document_imports
        SET status = 'confirmed', draft = %s, checks = %s, confirmed_by = app.current_user_id(), confirmed_at = now()
        WHERE id = %s
        """,
        (Jsonb(draft), Jsonb(checks), import_id),
    )
    return _detail(conn, import_id)


def _remember_document_name(conn: Connection, customer_id: str, issuer_name: str | None) -> None:
    """注文書上の名前を取引先に覚えておき、次からは完全一致で候補に出す。"""
    name = ic.clean_text(issuer_name)
    if not name:
        return
    customer = conn.execute("SELECT name, document_names FROM app.customers WHERE id = %s", (customer_id,)).fetchone()
    if customer is None or name == customer["name"]:
        return
    if normalize_company(name) in {normalize_company(n) for n in customer["document_names"]}:
        return
    conn.execute(
        "UPDATE app.customers SET document_names = array_append(document_names, %s) WHERE id = %s", (name, customer_id)
    )
