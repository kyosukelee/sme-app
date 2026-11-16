"""見積の API。

作成中 → 発行済(採番・控えPDFの保存)→ 受注済 / 失注 の流れを扱う。
金額は保存のたびにサーバーで計算し直し、画面から送られた金額は使わない。
受注への変換は orders.py にある。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from psycopg import Connection
from pydantic import BaseModel, Field

from app import storage
from app.auth import TenantConn
from app.clock import today
from app.documents import company, get_customer, pdf_response, read_lines, write_lines
from app.fields import OptionalHalfwidthStr
from app.pdf.render import render_quote
from app.pricing import price
from app.schemas import LineIn, LineOut, TaxSummaryOut

router = APIRouter(prefix="/api/quotes", tags=["quotes"])

ListFilter = Literal["all", "draft", "issued", "ordered", "closed"]


class QuoteIn(BaseModel):
    customer_id: UUID
    title: OptionalHalfwidthStr = Field(default=None, max_length=200)
    remarks: OptionalHalfwidthStr = Field(default=None, max_length=2000)
    lines: list[LineIn] = Field(min_length=1, max_length=100)


class QuoteListItem(BaseModel):
    id: UUID
    quote_number: str | None
    status: Literal["draft", "issued", "ordered", "lost"]
    is_expired: bool
    customer_name: str
    title: str | None
    issue_date: date | None
    valid_until: date | None
    total_amount: Decimal


class QuoteOut(QuoteListItem):
    customer_id: UUID
    customer_honorific: str
    customer_address: str | None
    remarks: str | None
    subtotal_amount: Decimal
    tax_amount: Decimal
    updated_at: datetime
    order_id: UUID | None  # 受注に変換済みなら、その受注
    lines: list[LineOut]
    tax_summaries: list[TaxSummaryOut]


QUOTE_COLUMNS = """
    id, tenant_id, quote_number, status, customer_id, customer_name, customer_honorific,
    customer_address, title, remarks, issue_date, valid_until,
    subtotal_amount, tax_amount, total_amount, updated_at,
    (status = 'issued' AND valid_until < %(today)s) AS is_expired
"""


def get_quote_record(conn: Connection, quote_id: UUID, *, for_update: bool = False) -> dict:
    lock = "FOR UPDATE" if for_update else ""
    quote = conn.execute(
        f"""
        SELECT {QUOTE_COLUMNS},
               (SELECT o.id FROM app.orders o WHERE o.quote_id = q.id) AS order_id
        FROM app.quotes q WHERE id = %(id)s {lock}
        """,
        {"id": quote_id, "today": today()},
    ).fetchone()
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "見積が見つかりません")
    quote["lines"], quote["tax_summaries"] = read_lines(conn, "quote", quote_id)
    return quote


def _require_draft(quote: dict, action: str) -> None:
    if quote["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, f"発行済みの見積は{action}できません")


@router.get("", response_model=list[QuoteListItem])
def list_quotes(conn: TenantConn, filter: ListFilter = "all"):
    """見積の一覧。closed は失注と期限切れ。"""
    return conn.execute(
        f"""
        SELECT {QUOTE_COLUMNS} FROM app.quotes
        WHERE %(f)s = 'all'
           OR (%(f)s = 'draft'   AND status = 'draft')
           OR (%(f)s = 'issued'  AND status = 'issued' AND valid_until >= %(today)s)
           OR (%(f)s = 'ordered' AND status = 'ordered')
           OR (%(f)s = 'closed'  AND (status = 'lost' OR (status = 'issued' AND valid_until < %(today)s)))
        ORDER BY (status = 'draft') DESC, issue_date DESC NULLS FIRST, created_at DESC
        LIMIT 200
        """,
        {"f": filter, "today": today()},
    ).fetchall()


@router.post("", response_model=QuoteOut, status_code=status.HTTP_201_CREATED)
def create_quote(body: QuoteIn, conn: TenantConn):
    customer = get_customer(conn, body.customer_id)
    pricing = price(conn, body.lines, today())
    quote_id = conn.execute(
        """
        INSERT INTO app.quotes
          (tenant_id, customer_id, customer_name, customer_honorific, customer_address, title, remarks,
           subtotal_amount, tax_amount, total_amount, created_by)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s, %s, app.current_user_id())
        RETURNING id
        """,
        (body.customer_id, customer["name"], customer["honorific"], customer["address"], body.title,
         body.remarks, pricing.totals.subtotal, pricing.totals.tax, pricing.totals.total),
    ).fetchone()["id"]
    write_lines(conn, "quote", quote_id, body.lines, pricing)
    return get_quote_record(conn, quote_id)


@router.get("/{quote_id}", response_model=QuoteOut)
def get_quote(quote_id: UUID, conn: TenantConn):
    return get_quote_record(conn, quote_id)


@router.put("/{quote_id}", response_model=QuoteOut)
def update_quote(quote_id: UUID, body: QuoteIn, conn: TenantConn):
    _require_draft(get_quote_record(conn, quote_id, for_update=True), "変更")
    customer = get_customer(conn, body.customer_id)
    pricing = price(conn, body.lines, today())
    conn.execute(
        """
        UPDATE app.quotes
        SET customer_id = %s, customer_name = %s, customer_honorific = %s, customer_address = %s,
            title = %s, remarks = %s, subtotal_amount = %s, tax_amount = %s, total_amount = %s
        WHERE id = %s
        """,
        (body.customer_id, customer["name"], customer["honorific"], customer["address"], body.title,
         body.remarks, pricing.totals.subtotal, pricing.totals.tax, pricing.totals.total, quote_id),
    )
    write_lines(conn, "quote", quote_id, body.lines, pricing)
    return get_quote_record(conn, quote_id)


@router.delete("/{quote_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quote(quote_id: UUID, conn: TenantConn):
    _require_draft(get_quote_record(conn, quote_id, for_update=True), "削除")
    conn.execute("DELETE FROM app.quotes WHERE id = %s", (quote_id,))


@router.post("/{quote_id}/issue", response_model=QuoteOut)
def issue_quote(quote_id: UUID, conn: TenantConn):
    """発行する。番号を採番し、発行日の税率で計算し直し、控えのPDFを保存する。"""
    quote = get_quote_record(conn, quote_id, for_update=True)
    _require_draft(quote, "再発行")

    issue_date = today()
    lines = [LineIn(**line) for line in quote["lines"]]
    pricing = price(conn, lines, issue_date)
    write_lines(conn, "quote", quote_id, lines, pricing)

    valid_days = conn.execute("SELECT quote_valid_days FROM app.tenant_settings").fetchone()["quote_valid_days"]
    number = conn.execute("SELECT app.next_document_number('quote', %s) AS n", (issue_date,)).fetchone()["n"]
    conn.execute(
        """
        UPDATE app.quotes
        SET quote_number = %s, issue_date = %s, valid_until = %s,
            subtotal_amount = %s, tax_amount = %s, total_amount = %s, status = 'issued'
        WHERE id = %s
        """,
        (number, issue_date, issue_date + timedelta(days=valid_days),
         pricing.totals.subtotal, pricing.totals.tax, pricing.totals.total, quote_id),
    )

    issued = get_quote_record(conn, quote_id)
    pdf = render_quote(issued, company(conn), draft=False)
    # DB の取り消しで同じ番号がもう一度使われても上書きしないよう、末尾に一意な文字を付ける
    key = f"{issued['tenant_id']}/quotes/{number}_v1_{uuid4().hex[:8]}.pdf"
    sha256 = storage.save(key, pdf)
    conn.execute(
        """
        INSERT INTO app.issued_documents
          (tenant_id, doc_type, document_id, document_number, version, storage_path, sha256,
           transaction_date, total_amount, customer_name, issued_by)
        VALUES (app.current_tenant_id(), 'quote', %s, %s, 1, %s, %s, %s, %s, %s, app.current_user_id())
        """,
        (quote_id, number, key, sha256, issue_date, issued["total_amount"], issued["customer_name"]),
    )
    return issued


@router.post("/{quote_id}/lose", response_model=QuoteOut)
def lose_quote(quote_id: UUID, conn: TenantConn):
    quote = get_quote_record(conn, quote_id, for_update=True)
    if quote["status"] != "issued":
        raise HTTPException(status.HTTP_409_CONFLICT, "失注にできるのは発行済みの見積だけです")
    conn.execute("UPDATE app.quotes SET status = 'lost' WHERE id = %s", (quote_id,))
    return get_quote_record(conn, quote_id)


@router.get("/{quote_id}/pdf")
def quote_pdf(quote_id: UUID, conn: TenantConn):
    """作成中ならプレビュー(下書きの透かし入り)、発行後は保存した控えをそのまま返す。"""
    quote = get_quote_record(conn, quote_id)
    if quote["status"] == "draft":
        return pdf_response(render_quote(quote, company(conn), draft=True), "見積書_下書き.pdf")

    doc = conn.execute(
        """
        SELECT storage_path FROM app.issued_documents
        WHERE doc_type = 'quote' AND document_id = %s
        ORDER BY version DESC LIMIT 1
        """,
        (quote_id,),
    ).fetchone()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "発行時の控えが見つかりません")
    return pdf_response(storage.read(doc["storage_path"]), f"見積書_{quote['quote_number']}.pdf")
