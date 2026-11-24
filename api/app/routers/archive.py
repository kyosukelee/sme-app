"""発行済み書類の検索(電子帳簿保存法の検索要件)。

発行時に保存した控え(issued_documents)を、取引年月日・取引金額・取引先で検索する。
日付と金額は範囲で指定でき、条件を組み合わせて検索できる。
控えのPDFは、保存時のハッシュ値と照らし合わせて書き換えられていないかを確かめられる。
"""

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app import storage
from app import activity
from app.auth import TenantConn
from app.documents import pdf_response
from app.domain.text import to_halfwidth

router = APIRouter(prefix="/api/documents", tags=["documents"])

DocType = Literal["quote", "order", "invoice"]
DOC_LABELS = {"quote": "見積書", "order": "注文請書", "invoice": "請求書"}


class IssuedDocument(BaseModel):
    id: UUID
    doc_type: DocType
    document_id: UUID  # 元の書類(見積・受注・請求書)
    document_number: str
    version: int
    transaction_date: date
    total_amount: Decimal
    customer_name: str
    issued_at: datetime
    superseded: bool  # 訂正で置き換わった請求書の旧版


class VerifyOut(BaseModel):
    ok: bool
    sha256_saved: str
    sha256_now: str | None
    message: str


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=list[IssuedDocument])
def search_documents(
    conn: TenantConn,
    doc_type: DocType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    customer: str | None = None,
    number: str | None = None,
):
    """条件はすべて省略でき、指定したものをすべて満たす控えを返す(新しい順、最大500件)。"""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "取引年月日の範囲が逆になっています")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "取引金額の範囲が逆になっています")

    conditions, params = [], {}
    if doc_type:
        conditions.append("d.doc_type = %(doc_type)s")
        params["doc_type"] = doc_type
    if date_from:
        conditions.append("d.transaction_date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        conditions.append("d.transaction_date <= %(date_to)s")
        params["date_to"] = date_to
    if amount_min is not None:
        conditions.append("d.total_amount >= %(amount_min)s")
        params["amount_min"] = amount_min
    if amount_max is not None:
        conditions.append("d.total_amount <= %(amount_max)s")
        params["amount_max"] = amount_max
    if customer and customer.strip():
        conditions.append("d.customer_name ILIKE %(customer)s ESCAPE '\\'")
        params["customer"] = f"%{_escape_like(to_halfwidth(customer.strip()))}%"
    if number and number.strip():
        conditions.append("d.document_number ILIKE %(number)s ESCAPE '\\'")
        params["number"] = f"%{_escape_like(to_halfwidth(number.strip()))}%"

    where = " AND ".join(conditions) or "true"
    return conn.execute(
        f"""
        SELECT d.id, d.doc_type, d.document_id, d.document_number, d.version, d.transaction_date,
               d.total_amount, d.customer_name, d.issued_at,
               (d.doc_type = 'invoice' AND i.superseded_at IS NOT NULL) AS superseded
        FROM app.issued_documents d
        LEFT JOIN app.invoices i ON d.doc_type = 'invoice' AND i.id = d.document_id
        WHERE {where}
        ORDER BY d.transaction_date DESC, d.issued_at DESC
        LIMIT 500
        """,
        params,
    ).fetchall()


def _get(conn, doc_id: UUID) -> dict:
    doc = conn.execute(
        "SELECT doc_type, document_number, version, storage_path, sha256 FROM app.issued_documents WHERE id = %s",
        (doc_id,),
    ).fetchone()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "控えが見つかりません")
    return doc


@router.get("/{doc_id}/pdf")
def document_pdf(doc_id: UUID, conn: TenantConn):
    doc = _get(conn, doc_id)
    name = f"{DOC_LABELS[doc['doc_type']]}_{doc['document_number']}_第{doc['version']}版.pdf"
    activity.record(conn, "open_document", f"{DOC_LABELS[doc['doc_type']]} {doc['document_number']} 第{doc['version']}版(書類の検索から)",
                    "issued_document", doc_id)
    return pdf_response(storage.read(doc["storage_path"]), name)


@router.get("/{doc_id}/verify", response_model=VerifyOut)
def verify_document(doc_id: UUID, conn: TenantConn):
    """保存している控えのPDFが、発行時から書き換えられていないかを確かめる。"""
    doc = _get(conn, doc_id)
    try:
        now = hashlib.sha256(storage.read(doc["storage_path"])).hexdigest()
    except FileNotFoundError:
        return VerifyOut(ok=False, sha256_saved=doc["sha256"], sha256_now=None,
                         message="控えのファイルが見つかりません")
    ok = now == doc["sha256"]
    return VerifyOut(
        ok=ok, sha256_saved=doc["sha256"], sha256_now=now,
        message="発行時の控えと一致しています(書き換えられていません)" if ok
        else "発行時の控えと一致しません。ファイルが書き換えられた可能性があります",
    )
