"""取引先ごとのまとめ入金。

取引先が複数の請求書の分をまとめて振り込んだとき、1回の入金として登録し、
未入金の請求書へ古い順(請求日の早い順)に割り当てる。
割り当ては請求書ごとの入金(payments)として記録するので、請求書の残額・入金済の判定は今までと同じ。

入金消込(bank.py)からは、割り当てる請求書を選び、振込手数料を差し引かれた差額(fee_amount)も
「振込手数料」として割り当てる。
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from psycopg import Connection
from pydantic import BaseModel, Field

from app.auth import TenantConn
from app.clock import today
from app.fields import OptionalHalfwidthStr
from app.routers.invoices import sync_paid_status

router = APIRouter(prefix="/api/receipts", tags=["receipts"])


class ReceiptIn(BaseModel):
    customer_id: UUID
    received_on: date
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=0)
    memo: OptionalHalfwidthStr = Field(default=None, max_length=200)
    dry_run: bool = False  # true なら割り当て方を返すだけで登録しない


class OpenInvoice(BaseModel):
    invoice_id: UUID
    invoice_number: str
    version: int
    title: str | None
    issue_date: date
    due_date: date | None
    total_amount: Decimal
    balance: Decimal
    is_overdue: bool


class OpenInvoicesOut(BaseModel):
    invoices: list[OpenInvoice]
    total_balance: Decimal


class Allocation(BaseModel):
    invoice_id: UUID
    invoice_number: str
    version: int
    amount: Decimal  # 入金として割り当てた額
    fee_amount: Decimal = Decimal(0)  # 振込手数料として消し込んだ額
    balance_after: Decimal  # 割り当てたあとの残額


class ReceiptOut(BaseModel):
    id: UUID | None  # dry_run のときは空
    customer_id: UUID
    customer_name: str
    received_on: date
    amount: Decimal
    fee_amount: Decimal = Decimal(0)
    memo: str | None
    created_at: datetime | None
    allocations: list[Allocation]


def get_any_customer(conn: Connection, customer_id: UUID) -> dict:
    """取引先を返す。使用中止にした取引先にも、未入金が残っていれば入金を登録できるようにする。"""
    customer = conn.execute("SELECT id, name FROM app.customers WHERE id = %s", (customer_id,)).fetchone()
    if customer is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "取引先が見つかりません")
    return customer


def open_invoice_rows(conn: Connection, customer_id: UUID, *, lock: bool = False) -> list[dict]:
    """取引先の未入金の請求書(発行済みで残額がある最新版)を、古い順に返す。"""
    rows = conn.execute(
        f"""
        SELECT i.id AS invoice_id, i.invoice_number, i.version, i.title, i.issue_date, i.due_date, i.total_amount,
               i.total_amount - i.withholding_amount
                 - coalesce((SELECT sum(p.amount) FROM app.payments p WHERE p.invoice_id = i.id), 0) AS balance,
               (i.due_date IS NOT NULL AND i.due_date < %s) AS is_overdue
        FROM app.invoices i
        WHERE i.customer_id = %s AND i.status = 'issued' AND i.superseded_at IS NULL
        ORDER BY i.issue_date, i.invoice_number
        {"FOR UPDATE OF i" if lock else ""}
        """,
        (today(), customer_id),
    ).fetchall()
    return [r for r in rows if r["balance"] > 0]


def _allocate(open_invoices: list[dict], amount: Decimal, fee: Decimal = Decimal(0)) -> list[dict]:
    """入金額を古い請求書から順に割り当て、振込手数料はそのあとの残りに割り当てる。"""
    allocations = []
    rest, fee_rest = amount, fee
    for inv in open_invoices:
        if rest <= 0 and fee_rest <= 0:
            break
        part = min(rest, inv["balance"])
        fee_part = min(fee_rest, inv["balance"] - part)
        if part == 0 and fee_part == 0:
            continue
        allocations.append({
            "invoice_id": inv["invoice_id"], "invoice_number": inv["invoice_number"], "version": inv["version"],
            "amount": part, "fee_amount": fee_part, "balance_after": inv["balance"] - part - fee_part,
        })
        rest -= part
        fee_rest -= fee_part
    return allocations


def record_receipt(
    conn: Connection,
    *,
    customer_id: UUID,
    received_on: date,
    amount: Decimal,
    memo: str | None,
    payment_memo: str,
    open_invoices: list[dict],
    fee: Decimal = Decimal(0),
) -> UUID:
    """入金を登録し、渡された請求書(古い順・ロック済み)に割り当てる。登録した入金の id を返す。"""
    receipt_id = conn.execute(
        """
        INSERT INTO app.receipts (tenant_id, customer_id, received_on, amount, fee_amount, memo, created_by)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, app.current_user_id())
        RETURNING id
        """,
        (customer_id, received_on, amount, fee, memo),
    ).fetchone()["id"]
    for a in _allocate(open_invoices, amount, fee):
        for kind, part, text in (("deposit", a["amount"], payment_memo), ("bank_fee", a["fee_amount"], "振込手数料")):
            if part > 0:
                conn.execute(
                    """
                    INSERT INTO app.payments (tenant_id, invoice_id, paid_on, amount, memo, receipt_id, kind, created_by)
                    VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, app.current_user_id())
                    """,
                    (a["invoice_id"], received_on, part, text, receipt_id, kind),
                )
        sync_paid_status(conn, a["invoice_id"])
    return receipt_id


def _get_receipt(conn: Connection, receipt_id: UUID) -> dict:
    receipt = conn.execute(
        """
        SELECT r.id, r.customer_id, c.name AS customer_name, r.received_on, r.amount, r.fee_amount, r.memo, r.created_at
        FROM app.receipts r JOIN app.customers c ON c.id = r.customer_id
        WHERE r.id = %s
        """,
        (receipt_id,),
    ).fetchone()
    if receipt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "入金が見つかりません")
    receipt["allocations"] = conn.execute(
        """
        SELECT p.invoice_id, i.invoice_number, i.version,
               sum(p.amount) FILTER (WHERE p.kind = 'deposit') AS amount,
               coalesce(sum(p.amount) FILTER (WHERE p.kind = 'bank_fee'), 0) AS fee_amount,
               i.total_amount - i.withholding_amount
                 - coalesce((SELECT sum(p2.amount) FROM app.payments p2 WHERE p2.invoice_id = i.id), 0) AS balance_after
        FROM app.payments p JOIN app.invoices i ON i.id = p.invoice_id
        WHERE p.receipt_id = %s
        GROUP BY p.invoice_id, i.id
        ORDER BY i.issue_date, i.invoice_number
        """,
        (receipt_id,),
    ).fetchall()
    return receipt


@router.get("/open-invoices", response_model=OpenInvoicesOut)
def open_invoices(customer_id: UUID, conn: TenantConn):
    get_any_customer(conn, customer_id)
    rows = open_invoice_rows(conn, customer_id)
    return OpenInvoicesOut(invoices=rows, total_balance=sum((r["balance"] for r in rows), Decimal(0)))


@router.get("", response_model=list[ReceiptOut])
def list_receipts(customer_id: UUID, conn: TenantConn):
    ids = conn.execute(
        "SELECT id FROM app.receipts WHERE customer_id = %s ORDER BY received_on DESC, created_at DESC LIMIT 100",
        (customer_id,),
    ).fetchall()
    return [_get_receipt(conn, r["id"]) for r in ids]


@router.post("", response_model=ReceiptOut)
def create_receipt(body: ReceiptIn, conn: TenantConn):
    """まとめ入金を登録し、古い請求書から順に割り当てる。dry_run なら割り当て方だけを返す。"""
    customer = get_any_customer(conn, body.customer_id)
    open_list = open_invoice_rows(conn, body.customer_id, lock=not body.dry_run)
    total = sum((r["balance"] for r in open_list), Decimal(0))
    if not open_list:
        raise HTTPException(status.HTTP_409_CONFLICT, "この取引先に未入金の請求書はありません")
    if body.amount > total:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"入金額が未入金の合計({total:,}円)を超えています。超える分は、どの請求書の入金か確認してから登録してください",
        )

    allocations = _allocate(open_list, body.amount)
    if body.dry_run:
        return ReceiptOut(id=None, customer_id=body.customer_id, customer_name=customer["name"],
                          received_on=body.received_on, amount=body.amount, memo=body.memo,
                          created_at=None, allocations=allocations)

    receipt_id = record_receipt(
        conn, customer_id=body.customer_id, received_on=body.received_on, amount=body.amount, memo=body.memo,
        payment_memo=f"まとめ入金{f'({body.memo})' if body.memo else ''}", open_invoices=open_list,
    )
    return _get_receipt(conn, receipt_id)


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_receipt(receipt_id: UUID, conn: TenantConn):
    """まとめ入金を取り消す。割り当てた請求書ごとの入金も一緒に消え、請求書は未入金に戻る。"""
    receipt = _get_receipt(conn, receipt_id)
    invoice_ids = [a["invoice_id"] for a in receipt["allocations"]]
    conn.execute("SELECT id FROM app.invoices WHERE id = ANY(%s::uuid[]) FOR UPDATE", (invoice_ids,))
    conn.execute("DELETE FROM app.receipts WHERE id = %s", (receipt_id,))
    for invoice_id in invoice_ids:
        sync_paid_status(conn, invoice_id)
