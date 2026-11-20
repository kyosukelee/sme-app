"""請求書の API。

作る入口は3つ: 受注から / 月締めのまとめ請求 / 直接作成。
作成中 → 発行済(採番・発行者情報のコピー・控えPDF)→ 入金済 / 取消。
発行後に誤りがあれば、同じ番号で版を上げた訂正版を作る(旧版と控えは残る)。

受注は明細の行ごとに請求状況を管理し、すべての行が発行済みの請求書に載ったら「請求済」にする。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from psycopg import Connection
from pydantic import BaseModel, Field, field_validator

from app import storage
from app.auth import TenantConn
from app.clock import today
from app.documents import company, get_customer, pdf_response, read_lines, write_lines
from app.domain.withholding import withholding_tax
from app.domain.terms import closing_date_on_or_after, due_date, latest_closing_date, previous_closing_date
from app.fields import OptionalHalfwidthStr
from app.pdf.render import render_invoice
from app.pricing import price
from app.schemas import InvoiceLineIn, LineOut, TaxSummaryOut

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

ListFilter = Literal["all", "draft", "unpaid", "paid", "void"]


# ---------------------------------------------------------------------
# 入出力の型
# ---------------------------------------------------------------------
class InvoiceIn(BaseModel):
    customer_id: UUID
    title: OptionalHalfwidthStr = Field(default=None, max_length=200)
    remarks: OptionalHalfwidthStr = Field(default=None, max_length=2000)
    due_date: date | None = None  # 省略すると締め日・支払日から決める
    withholding: bool | None = None  # 源泉徴収するか。省略すると取引先の設定(作成時)またはいまの設定(変更時)
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=200)


class FromOrdersIn(BaseModel):
    customer_id: UUID
    closing_date: date | None = None  # 締め請求のときの締め日
    order_line_ids: list[UUID] = Field(min_length=1, max_length=200)


class PaymentIn(BaseModel):
    paid_on: date
    amount: Decimal = Field(max_digits=15, decimal_places=0)  # 入金はプラス、返金(過入金の払い戻し)はマイナス
    memo: OptionalHalfwidthStr = Field(default=None, max_length=200)

    @field_validator("amount")
    @classmethod
    def amount_is_not_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("金額を入力してください")
        return value


class InvoiceLineOut(LineOut):
    order_line_id: UUID | None
    transaction_date: date
    date_confirmed: bool


class PaymentOut(BaseModel):
    id: UUID
    paid_on: date
    amount: Decimal
    memo: str | None
    created_at: datetime
    receipt_id: UUID | None  # まとめ入金から割り当てた入金なら、その入金
    kind: str = "deposit"  # deposit=入金、bank_fee=振込手数料として消し込んだ額


class CarryoverOut(BaseModel):
    source_invoice_id: UUID
    source_number: str
    source_version: int
    source_issue_date: date
    balance: Decimal


class VersionOut(BaseModel):
    id: UUID
    version: int
    status: str
    issue_date: date | None
    superseded_at: datetime | None


class OrderRef(BaseModel):
    id: UUID
    order_number: str


class InvoiceListItem(BaseModel):
    id: UUID
    invoice_number: str | None
    version: int
    status: Literal["draft", "issued", "paid", "void"]
    customer_name: str
    title: str | None
    issue_date: date | None
    due_date: date | None
    total_amount: Decimal
    withholding: bool
    withholding_amount: Decimal  # 源泉徴収税額
    net_amount: Decimal          # 差引ご請求額 = 合計 − 源泉徴収税額(入金の基準)
    paid_amount: Decimal
    balance: Decimal             # 差引ご請求額 − 入金
    is_overdue: bool


class InvoiceOut(InvoiceListItem):
    customer_id: UUID
    customer_honorific: str
    customer_address: str | None
    remarks: str | None
    subtotal_amount: Decimal
    tax_amount: Decimal
    issuer_name: str | None
    issuer_registration_number: str | None
    issuer_representative: str | None
    issuer_tax_exempt: bool
    previous_version_id: UUID | None
    superseded_at: datetime | None
    voided_at: datetime | None
    updated_at: datetime
    correction_draft_id: UUID | None  # 作成中の訂正版があれば、その請求書
    closing_date: date | None         # 締め請求で作った請求書の締め日(締め請求以外は空)
    carried_over_amount: Decimal      # 前回までの未入金(繰越)。作成中は今の時点の見込み
    carryovers: list[CarryoverOut]    # 繰越の内訳
    amount_due: Decimal               # 今回ご請求額 = 繰越 + 差引ご請求額
    default_due_date: date | None     # 支払期限が空欄のときに使う日付(最も遅い取引日の締め期間から計算)
    lines: list[InvoiceLineOut]
    tax_summaries: list[TaxSummaryOut]
    payments: list[PaymentOut]
    versions: list[VersionOut]
    orders: list[OrderRef]


class BillableLine(BaseModel):
    order_id: UUID
    order_number: str
    order_title: str | None
    order_line_id: UUID
    line_no: int
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    amount: Decimal
    tax_category: str
    delivered_on: date


class BillableOut(BaseModel):
    closing_date: date
    period_start: date  # 今回の締め期間の初日(これより前の未請求分も含めて返す)
    due_date: date
    lines: list[BillableLine]
    missing_delivery_date: int  # 納品日が未入力で対象にできない明細の数


# ---------------------------------------------------------------------
# 内部の処理
# ---------------------------------------------------------------------
INVOICE_COLUMNS = """
    i.id, i.tenant_id, i.invoice_number, i.version, i.previous_version_id, i.superseded_at, i.status,
    i.customer_id, i.customer_name, i.customer_honorific, i.customer_address,
    i.issuer_name, i.issuer_registration_number, i.title, i.remarks, i.issue_date, i.due_date,
    i.subtotal_amount, i.tax_amount, i.total_amount, i.voided_at, i.updated_at,
    i.closing_date, i.carried_over_amount, i.withholding, i.withholding_amount,
    i.issuer_representative, i.issuer_tax_exempt,
    coalesce((SELECT sum(p.amount) FROM app.payments p WHERE p.invoice_id = i.id), 0) AS paid_amount
"""


def _finish_row(row: dict) -> dict:
    row["net_amount"] = row["total_amount"] - row["withholding_amount"]
    row["balance"] = row["net_amount"] - row["paid_amount"]
    row["is_overdue"] = row["status"] == "issued" and row["due_date"] is not None and row["due_date"] < today()
    return row


def get_invoice_record(conn: Connection, invoice_id: UUID, *, for_update: bool = False) -> dict:
    lock = "FOR UPDATE OF i" if for_update else ""
    invoice = conn.execute(f"SELECT {INVOICE_COLUMNS} FROM app.invoices i WHERE i.id = %s {lock}", (invoice_id,)).fetchone()
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "請求書が見つかりません")
    _finish_row(invoice)
    invoice["lines"], invoice["tax_summaries"] = read_lines(conn, "invoice", invoice_id)
    invoice["payments"] = conn.execute(
        "SELECT id, paid_on, amount, memo, created_at, receipt_id, kind FROM app.payments WHERE invoice_id = %s ORDER BY paid_on, created_at",
        (invoice_id,),
    ).fetchall()
    invoice["versions"] = (
        conn.execute(
            """
            SELECT id, version, status, issue_date, superseded_at FROM app.invoices
            WHERE invoice_number = %s ORDER BY version
            """,
            (invoice["invoice_number"],),
        ).fetchall()
        if invoice["invoice_number"]
        else []
    )
    invoice["orders"] = conn.execute(
        """
        SELECT DISTINCT o.id, o.order_number
        FROM app.invoice_lines il
        JOIN app.order_lines ol ON ol.id = il.order_line_id
        JOIN app.orders o ON o.id = ol.order_id
        WHERE il.invoice_id = %s ORDER BY o.order_number
        """,
        (invoice_id,),
    ).fetchall()
    draft = conn.execute(
        "SELECT id FROM app.invoices WHERE previous_version_id = %s AND status = 'draft'", (invoice_id,)
    ).fetchone()
    invoice["correction_draft_id"] = draft["id"] if draft else None
    # 支払期限が空欄のまま発行した場合に入る日付(画面の案内とプレビューに使う)
    invoice["default_due_date"] = _due_date_for(
        conn, invoice["customer_id"], transaction_dates=[line["transaction_date"] for line in invoice["lines"]]
    )
    if invoice["status"] == "draft":
        # 作成中は、いまの時点の未入金から繰越を見込みで出す(発行時に確定する)
        invoice["carryovers"] = _open_balances_for_carryover(conn, invoice)
        invoice["carried_over_amount"] = sum((c["balance"] for c in invoice["carryovers"]), Decimal(0))
    else:
        invoice["carryovers"] = conn.execute(
            """
            SELECT source_invoice_id, source_number, source_version, source_issue_date, balance
            FROM app.invoice_carryovers WHERE invoice_id = %s ORDER BY source_issue_date, source_number
            """,
            (invoice_id,),
        ).fetchall()
    invoice["amount_due"] = invoice["carried_over_amount"] + invoice["net_amount"]
    return invoice


def _open_balances_for_carryover(conn: Connection, invoice: dict) -> list[dict]:
    """締め請求の請求書に繰越として印字する、同じ取引先の未入金。締め請求以外は空。

    対象は、発行済みで残額がある最新版の請求書(この請求書と、訂正元の版は除く)。
    繰越は金額のお知らせで、インボイスの明細には含めない。
    """
    if invoice["closing_date"] is None:
        return []
    return conn.execute(
        """
        SELECT i.id AS source_invoice_id, i.invoice_number AS source_number, i.version AS source_version,
               i.issue_date AS source_issue_date,
               i.total_amount - i.withholding_amount
                 - coalesce((SELECT sum(p.amount) FROM app.payments p WHERE p.invoice_id = i.id), 0) AS balance
        FROM app.invoices i
        WHERE i.customer_id = %(c)s AND i.status = 'issued' AND i.superseded_at IS NULL
          AND i.id <> %(me)s AND i.id IS DISTINCT FROM %(prev)s
          AND i.total_amount - i.withholding_amount
              - coalesce((SELECT sum(p.amount) FROM app.payments p WHERE p.invoice_id = i.id), 0) > 0
        ORDER BY i.issue_date, i.invoice_number
        """,
        {"c": invoice["customer_id"], "me": invoice["id"], "prev": invoice["previous_version_id"]},
    ).fetchall()


def _terms(conn: Connection, customer_id: UUID) -> tuple[int, int, int]:
    """取引先の締め日・支払日(未設定なら会社設定)。"""
    row = conn.execute(
        """
        SELECT coalesce(c.closing_day, s.closing_day) AS closing_day,
               coalesce(c.payment_month_offset, s.payment_month_offset) AS payment_month_offset,
               coalesce(c.payment_day, s.payment_day) AS payment_day
        FROM app.customers c CROSS JOIN app.tenant_settings s
        WHERE c.id = %s
        """,
        (customer_id,),
    ).fetchone()
    return row["closing_day"], row["payment_month_offset"], row["payment_day"]


def _due_date_for(conn: Connection, customer_id: UUID, *, closing: date | None = None,
                  transaction_dates: list[date] | None = None) -> date | None:
    """支払期限の自動計算。締め日が決まっていればそこから、なければ最も遅い取引日が属する締め期間から決める。"""
    closing_day, offset, payment_day = _terms(conn, customer_id)
    if closing is None:
        if not transaction_dates:
            return None
        closing = closing_date_on_or_after(max(transaction_dates), closing_day)
    return due_date(closing, offset, payment_day)


def _check_order_lines(conn: Connection, lines: list[InvoiceLineIn], customer_id: UUID, *,
                       allowed_invoice_ids: list[UUID], lock_amounts: bool = True) -> None:
    """受注明細を参照する行が、同じ取引先の受注のもので、他の請求書にまだ載っていないことを確かめる。

    lock_amounts のときは、数量・単価・税率が受注と同じであることも確かめる。受注は行単位で
    請求済みかどうかを管理するため、請求書側で数量を減らすと残りを請求できなくなるからである。
    訂正版は誤りを直すためのものなので、この確認を外す。
    """
    ids = [line.order_line_id for line in lines if line.order_line_id]
    if not ids:
        return
    if len(ids) != len(set(ids)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "同じ受注明細が2回含まれています")
    rows = conn.execute(
        """
        SELECT ol.id, o.customer_id, o.order_number, ol.quantity, ol.unit_price, ol.tax_category,
               EXISTS (SELECT 1 FROM app.order_line_billing b
                       WHERE b.order_line_id = ol.id AND NOT (b.invoice_id = ANY(%s))) AS taken
        FROM app.order_lines ol JOIN app.orders o ON o.id = ol.order_id
        WHERE ol.id = ANY(%s)
        """,
        (allowed_invoice_ids, ids),
    ).fetchall()
    if len(rows) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "受注明細が見つかりません")
    by_id = {row["id"]: row for row in rows}
    for no, line in enumerate(lines, start=1):
        row = by_id.get(line.order_line_id)
        if row is None:
            continue
        if row["customer_id"] != customer_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"受注 {row['order_number']} は別の取引先のものです")
        if row["taken"]:
            raise HTTPException(status.HTTP_409_CONFLICT, f"受注 {row['order_number']} の明細は、すでに別の請求書に載っています")
        if lock_amounts and (line.quantity, line.unit_price, line.tax_category) != (
            row["quantity"], row["unit_price"], row["tax_category"]
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{no}行目は受注 {row['order_number']} から作った明細のため、数量・単価・税率は変更できません。"
                "変更が必要なときは、この行を請求書から外して受注を直してください",
            )


def _order_ids(conn: Connection, invoice_ids: list[UUID]) -> list[UUID]:
    return [
        r["order_id"]
        for r in conn.execute(
            """
            SELECT DISTINCT ol.order_id FROM app.invoice_lines il
            JOIN app.order_lines ol ON ol.id = il.order_line_id
            WHERE il.invoice_id = ANY(%s)
            """,
            (invoice_ids,),
        ).fetchall()
    ]


def sync_order_status(conn: Connection, order_ids: list[UUID]) -> None:
    """すべての明細が発行済みの請求書に載った受注を「請求済」に、そうでなければ「未請求」にする。"""
    if not order_ids:
        return
    conn.execute(
        """
        UPDATE app.orders o SET status = x.new_status
        FROM (
          SELECT o2.id,
                 CASE WHEN NOT EXISTS (
                        SELECT 1 FROM app.order_lines ol
                        WHERE ol.order_id = o2.id
                          AND NOT EXISTS (SELECT 1 FROM app.order_line_billing b
                                          WHERE b.order_line_id = ol.id AND b.invoice_status IN ('issued', 'paid')))
                      THEN 'billed' ELSE 'open' END AS new_status
          FROM app.orders o2 WHERE o2.id = ANY(%s)
        ) x
        WHERE o.id = x.id AND o.status <> x.new_status
        """,
        (order_ids,),
    )


def sync_paid_status(conn: Connection, invoice_id: UUID) -> None:
    """入金の合計が差引ご請求額(合計 − 源泉徴収税額)に達していれば「入金済」、届いていなければ「発行済」にする。"""
    conn.execute(
        """
        UPDATE app.invoices i
        SET status = CASE WHEN coalesce((SELECT sum(amount) FROM app.payments p WHERE p.invoice_id = i.id), 0)
                               >= i.total_amount - i.withholding_amount THEN 'paid' ELSE 'issued' END
        WHERE i.id = %s AND i.status IN ('issued', 'paid')
        """,
        (invoice_id,),
    )


def _is_sole_proprietor(conn: Connection) -> bool:
    row = conn.execute("SELECT business_type FROM app.company_profiles").fetchone()
    return bool(row and row["business_type"] == "sole_proprietor")


def _resolve_withholding(conn: Connection, requested: bool | None, *, customer_id: UUID, current: bool | None = None) -> bool:
    """源泉徴収するかを決める。指定がなければ、変更時はいまの設定、作成時は取引先の設定に従う。

    源泉徴収は個人事業主が受け取る報酬にかかるもので、法人には関係しない。
    法人のときに指定されたら 422 にし、取引先の初期値(個人事業主のときに設定したもの)は無視する。
    """
    sole = _is_sole_proprietor(conn)
    if requested is None:
        if current is not None:
            return current and sole
        row = conn.execute("SELECT withholding_default FROM app.customers WHERE id = %s", (customer_id,)).fetchone()
        return bool(row and row["withholding_default"]) and sole
    if requested and not sole:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "源泉徴収は、自社情報の事業形態が「個人事業主」のときだけ使えます",
        )
    return requested


def _withholding_amount(withholding: bool, subtotal: Decimal) -> Decimal:
    return withholding_tax(subtotal) if withholding else Decimal(0)


def _insert_invoice(conn: Connection, body: InvoiceIn, customer: dict, *, number: str | None = None,
                    version: int = 1, previous_version_id: UUID | None = None,
                    closing_date: date | None = None) -> UUID:
    pricing = price(conn, body.lines, today())
    withholding = _resolve_withholding(conn, body.withholding, customer_id=body.customer_id)
    invoice_id = conn.execute(
        """
        INSERT INTO app.invoices
          (tenant_id, invoice_number, version, previous_version_id, customer_id, customer_name, customer_honorific,
           customer_address, title, remarks, due_date, closing_date, subtotal_amount, tax_amount, total_amount,
           withholding, withholding_amount, created_by)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                app.current_user_id())
        RETURNING id
        """,
        (number, version, previous_version_id, body.customer_id, customer["name"], customer["honorific"],
         customer["address"], body.title, body.remarks, body.due_date, closing_date,
         pricing.totals.subtotal, pricing.totals.tax, pricing.totals.total,
         withholding, _withholding_amount(withholding, pricing.totals.subtotal)),
    ).fetchone()["id"]
    write_lines(conn, "invoice", invoice_id, body.lines, pricing)
    return invoice_id


def _require_draft(invoice: dict, action: str) -> None:
    if invoice["status"] != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, f"発行済みの請求書は{action}できません。訂正版を作成してください")


def _available_order_lines(conn: Connection, where: str, params: dict) -> list[dict]:
    """まだどの請求書にも載っていない受注明細。"""
    return conn.execute(
        f"""
        SELECT o.id AS order_id, o.order_number, o.title AS order_title, o.order_date, o.delivery_due_date,
               ol.id AS order_line_id, ol.line_no, ol.description, ol.quantity, ol.unit, ol.unit_price,
               ol.amount, ol.tax_category, ol.delivered_on
        FROM app.order_lines ol JOIN app.orders o ON o.id = ol.order_id
        WHERE NOT EXISTS (SELECT 1 FROM app.order_line_billing b WHERE b.order_line_id = ol.id)
          AND {where}
        ORDER BY ol.delivered_on NULLS LAST, o.order_number, ol.line_no
        """,
        params,
    ).fetchall()


def _line_from_order(row: dict, transaction_date: date, *, confirmed: bool = True) -> InvoiceLineIn:
    return InvoiceLineIn(
        description=row["description"], quantity=row["quantity"], unit=row["unit"], unit_price=row["unit_price"],
        tax_category=row["tax_category"], order_line_id=row["order_line_id"], transaction_date=transaction_date,
        date_confirmed=confirmed,
    )


def _lock_orders(conn: Connection, *, order_line_ids: list[UUID] = (), order_ids: list[UUID] = ()) -> None:
    """請求書に載せる受注明細の受注を行ロックする。

    2人が同時に同じ受注明細で請求書を作ると、どちらも「未請求」と判定して二重に請求できてしまう。
    受注をロックしてから未請求かどうかを確かめることで、後から来た操作は先の操作が終わるのを待ち、
    その結果(すでに請求書に載っている)を見て止まる。受注の変更(orders.py)も同じ行をロックする。
    ロックの順番をそろえて、デッドロックを避ける。
    """
    if not order_line_ids and not order_ids:
        return
    conn.execute(
        """
        SELECT id FROM app.orders
        WHERE id = ANY(%s::uuid[]) OR id IN (SELECT order_id FROM app.order_lines WHERE id = ANY(%s::uuid[]))
        ORDER BY id FOR UPDATE
        """,
        (list(order_ids), list(order_line_ids)),
    )


# ---------------------------------------------------------------------
# 一覧・作成
# ---------------------------------------------------------------------
@router.get("", response_model=list[InvoiceListItem])
def list_invoices(conn: TenantConn, filter: ListFilter = "all"):
    """請求書の一覧(訂正で置き換わった旧版は出さない)。unpaid は発行済みで入金が済んでいないもの。"""
    status_by_filter = {"draft": "draft", "unpaid": "issued", "paid": "paid", "void": "void"}
    rows = conn.execute(
        f"""
        SELECT {INVOICE_COLUMNS} FROM app.invoices i
        WHERE i.superseded_at IS NULL AND (%(s)s::text IS NULL OR i.status = %(s)s)
        ORDER BY (i.status = 'draft') DESC, i.issue_date DESC NULLS FIRST, i.created_at DESC
        LIMIT 200
        """,
        {"s": status_by_filter.get(filter)},
    ).fetchall()
    return [_finish_row(r) for r in rows]


@router.get("/billable-lines", response_model=BillableOut)
def billable_lines(customer_id: UUID, conn: TenantConn, closing_date: date | None = None):
    """締め請求の対象になる受注明細。締め日までに納品した未請求の明細(前の期間の請求漏れも含む)。"""
    get_customer(conn, customer_id)
    closing_day, offset, payment_day = _terms(conn, customer_id)
    closing = closing_date or latest_closing_date(today(), closing_day)
    start = previous_closing_date(closing_date_on_or_after(closing, closing_day), closing_day) + timedelta(days=1)

    lines = _available_order_lines(
        conn, "o.customer_id = %(c)s AND ol.delivered_on <= %(d)s", {"c": customer_id, "d": closing}
    )
    missing = conn.execute(
        """
        SELECT count(*) AS n FROM app.order_lines ol JOIN app.orders o ON o.id = ol.order_id
        WHERE o.customer_id = %s AND ol.delivered_on IS NULL
          AND NOT EXISTS (SELECT 1 FROM app.order_line_billing b WHERE b.order_line_id = ol.id)
        """,
        (customer_id,),
    ).fetchone()["n"]
    return BillableOut(
        closing_date=closing, period_start=start, due_date=due_date(closing, offset, payment_day),
        lines=lines, missing_delivery_date=missing,
    )


@router.post("/from-orders", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_from_order_lines(body: FromOrdersIn, conn: TenantConn):
    """選んだ受注明細から請求書を作る(締め請求)。取引日は各明細の納品日。"""
    customer = get_customer(conn, body.customer_id)
    _lock_orders(conn, order_line_ids=body.order_line_ids)
    rows = {r["order_line_id"]: r for r in _available_order_lines(conn, "ol.id = ANY(%(ids)s)", {"ids": body.order_line_ids})}
    missing = [i for i in body.order_line_ids if i not in rows]
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT, "選んだ明細の一部は、すでに請求書に載っているか見つかりません")
    if any(r["delivered_on"] is None for r in rows.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "納品日が未入力の明細は締め請求に含められません")

    lines = [_line_from_order(rows[i], rows[i]["delivered_on"]) for i in body.order_line_ids]
    title = f"{body.closing_date.year}年{body.closing_date.month}月締め分" if body.closing_date else None
    due = _due_date_for(conn, body.customer_id, closing=body.closing_date) if body.closing_date else None
    invoice_in = InvoiceIn(customer_id=body.customer_id, title=title, due_date=due, lines=lines)
    _check_order_lines(conn, invoice_in.lines, body.customer_id, allowed_invoice_ids=[])
    return get_invoice_record(conn, _insert_invoice(conn, invoice_in, customer, closing_date=body.closing_date))


@router.post("/from-order/{order_id}", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_from_order(order_id: UUID, conn: TenantConn):
    """受注のまだ請求していない明細から請求書を作る。

    取引日は納品日。未入力なら納期、それもなければ受注日を仮に入れる(作成中に直せる)。
    """
    order = conn.execute("SELECT customer_id, title FROM app.orders WHERE id = %s", (order_id,)).fetchone()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "受注が見つかりません")
    _lock_orders(conn, order_ids=[order_id])
    rows = _available_order_lines(conn, "o.id = %(o)s", {"o": order_id})
    if not rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "この受注に、まだ請求していない明細はありません")

    # 納品日がない行は、納期か受注日を仮の取引日として入れ、確認するまで発行できないようにする
    lines = [
        _line_from_order(r, r["delivered_on"] or r["delivery_due_date"] or r["order_date"],
                         confirmed=r["delivered_on"] is not None)
        for r in rows
    ]
    customer = get_customer(conn, order["customer_id"])
    invoice_in = InvoiceIn(customer_id=order["customer_id"], title=order["title"], lines=lines)
    return get_invoice_record(conn, _insert_invoice(conn, invoice_in, customer))


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceIn, conn: TenantConn):
    """受注を通さずに請求書を作る。"""
    customer = get_customer(conn, body.customer_id)
    _lock_orders(conn, order_line_ids=[l.order_line_id for l in body.lines if l.order_line_id])
    _check_order_lines(conn, body.lines, body.customer_id, allowed_invoice_ids=[])
    return get_invoice_record(conn, _insert_invoice(conn, body, customer))


# ---------------------------------------------------------------------
# 1件の操作
# ---------------------------------------------------------------------
@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: UUID, conn: TenantConn):
    return get_invoice_record(conn, invoice_id)


@router.put("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(invoice_id: UUID, body: InvoiceIn, conn: TenantConn):
    invoice = get_invoice_record(conn, invoice_id, for_update=True)
    _require_draft(invoice, "変更")
    allowed = [invoice_id] + ([invoice["previous_version_id"]] if invoice["previous_version_id"] else [])
    _lock_orders(conn, order_line_ids=[l.order_line_id for l in body.lines if l.order_line_id])
    _check_order_lines(conn, body.lines, body.customer_id, allowed_invoice_ids=allowed,
                       lock_amounts=invoice["version"] == 1)
    customer = get_customer(conn, body.customer_id)
    pricing = price(conn, body.lines, today())
    withholding = _resolve_withholding(conn, body.withholding, customer_id=body.customer_id, current=invoice["withholding"])
    conn.execute(
        """
        UPDATE app.invoices
        SET customer_id = %s, customer_name = %s, customer_honorific = %s, customer_address = %s,
            title = %s, remarks = %s, due_date = %s, subtotal_amount = %s, tax_amount = %s, total_amount = %s,
            withholding = %s, withholding_amount = %s
        WHERE id = %s
        """,
        (body.customer_id, customer["name"], customer["honorific"], customer["address"], body.title, body.remarks,
         body.due_date,
         pricing.totals.subtotal, pricing.totals.tax, pricing.totals.total,
         withholding, _withholding_amount(withholding, pricing.totals.subtotal), invoice_id),
    )
    write_lines(conn, "invoice", invoice_id, body.lines, pricing)
    return get_invoice_record(conn, invoice_id)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(invoice_id: UUID, conn: TenantConn):
    """作成中の請求書を削除する。載せていた受注明細は、また請求できる状態に戻る。"""
    _require_draft(get_invoice_record(conn, invoice_id, for_update=True), "削除")
    conn.execute("DELETE FROM app.invoices WHERE id = %s", (invoice_id,))


@router.post("/{invoice_id}/issue", response_model=InvoiceOut)
def issue_invoice(invoice_id: UUID, conn: TenantConn):
    """発行する。番号の採番(訂正版は元の番号のまま)、発行者情報のコピー、控えPDFの保存を行う。"""
    invoice = get_invoice_record(conn, invoice_id, for_update=True)
    _require_draft(invoice, "再発行")
    profile = company(conn)
    if profile is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "自社情報が登録されていません。設定の「自社情報」から登録してください")

    previous = None
    if invoice["previous_version_id"]:
        previous = get_invoice_record(conn, invoice["previous_version_id"], for_update=True)
        if previous["superseded_at"] is not None or previous["status"] not in ("issued", "paid"):
            raise HTTPException(status.HTTP_409_CONFLICT, "訂正元の請求書が、訂正できる状態ではありません")

    unconfirmed = [line["line_no"] for line in invoice["lines"] if not line["date_confirmed"]]
    if unconfirmed:
        rows = "・".join(f"{n}行目" for n in unconfirmed)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{rows}の取引日は仮の日付です(受注に納品日がなかったため)。取引日を確認してから発行してください",
        )

    issue_date = today()
    lines = [InvoiceLineIn(**line) for line in invoice["lines"]]
    pricing = price(conn, lines, issue_date)
    write_lines(conn, "invoice", invoice_id, lines, pricing)
    number = invoice["invoice_number"] or conn.execute(
        "SELECT app.next_document_number('invoice', %s) AS n", (issue_date,)
    ).fetchone()["n"]

    # 繰越は発行時点の未入金で確定し、内訳を写しとして残す(作成中のうちに書き込む)
    carryovers = _open_balances_for_carryover(conn, invoice)
    conn.execute("DELETE FROM app.invoice_carryovers WHERE invoice_id = %s", (invoice_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO app.invoice_carryovers
              (tenant_id, invoice_id, source_invoice_id, source_number, source_version, source_issue_date, balance)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s)
            """,
            [(invoice_id, c["source_invoice_id"], c["source_number"], c["source_version"],
              c["source_issue_date"], c["balance"]) for c in carryovers],
        )
    carried_over = sum((c["balance"] for c in carryovers), Decimal(0))

    if invoice["withholding"] and profile["business_type"] != "sole_proprietor":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "源泉徴収は個人事業主のときだけ使えます。自社情報の事業形態を確認するか、源泉徴収を外してください",
        )

    conn.execute(
        """
        UPDATE app.invoices
        SET invoice_number = %s, issue_date = %s, due_date = %s, issuer_name = %s, issuer_registration_number = %s,
            issuer_representative = %s, issuer_tax_exempt = %s,
            subtotal_amount = %s, tax_amount = %s, total_amount = %s, carried_over_amount = %s,
            withholding_amount = %s, status = 'issued', issued_at = now()
        WHERE id = %s
        """,
        (number, issue_date,
         invoice["due_date"] or _due_date_for(conn, invoice["customer_id"], transaction_dates=[l.transaction_date for l in lines]),
         profile["name"], profile["registration_number"], profile["representative_name"], profile["tax_exempt"],
         pricing.totals.subtotal, pricing.totals.tax, pricing.totals.total, carried_over,
         _withholding_amount(invoice["withholding"], pricing.totals.subtotal), invoice_id),
    )

    if previous:
        # 旧版に訂正済みの印を付け、入金は新しい版に付け替える
        conn.execute("UPDATE app.invoices SET superseded_at = now() WHERE id = %s", (previous["id"],))
        conn.execute("UPDATE app.payments SET invoice_id = %s WHERE invoice_id = %s", (invoice_id, previous["id"]))
        sync_paid_status(conn, invoice_id)

    issued = get_invoice_record(conn, invoice_id)
    pdf = render_invoice(issued, profile, preview=False, previous=previous)
    key = f"{issued['tenant_id']}/invoices/{number}_v{issued['version']}_{uuid4().hex[:8]}.pdf"
    sha256 = storage.save(key, pdf)
    conn.execute(
        """
        INSERT INTO app.issued_documents
          (tenant_id, doc_type, document_id, document_number, version, storage_path, sha256,
           transaction_date, total_amount, customer_name, issued_by)
        VALUES (app.current_tenant_id(), 'invoice', %s, %s, %s, %s, %s, %s, %s, %s, app.current_user_id())
        """,
        (invoice_id, number, issued["version"], key, sha256, issue_date, issued["total_amount"], issued["customer_name"]),
    )

    sync_order_status(conn, _order_ids(conn, [invoice_id] + ([previous["id"]] if previous else [])))
    return get_invoice_record(conn, invoice_id)


@router.post("/{invoice_id}/corrections", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_correction(invoice_id: UUID, conn: TenantConn):
    """発行済みの請求書の訂正版を、同じ番号・次の版の作成中として作る。"""
    invoice = get_invoice_record(conn, invoice_id, for_update=True)
    if invoice["status"] not in ("issued", "paid") or invoice["superseded_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "訂正版を作れるのは、最新版の発行済みの請求書だけです")
    if invoice["correction_draft_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "作成中の訂正版がすでにあります")

    body = InvoiceIn(
        customer_id=invoice["customer_id"], title=invoice["title"], remarks=invoice["remarks"],
        due_date=invoice["due_date"], withholding=invoice["withholding"],
        lines=[InvoiceLineIn(**line) for line in invoice["lines"]],
    )
    customer = {"name": invoice["customer_name"], "honorific": invoice["customer_honorific"],
                "address": invoice["customer_address"]}
    new_id = _insert_invoice(conn, body, customer, number=invoice["invoice_number"],
                             version=invoice["version"] + 1, previous_version_id=invoice_id,
                             closing_date=invoice["closing_date"])
    return get_invoice_record(conn, new_id)


@router.post("/{invoice_id}/void", response_model=InvoiceOut)
def void_invoice(invoice_id: UUID, conn: TenantConn):
    """取引がなくなった請求書を取り消す。受注は「未請求」に戻る。"""
    invoice = get_invoice_record(conn, invoice_id, for_update=True)
    if invoice["status"] != "issued" or invoice["superseded_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "取り消せるのは、入金のない最新版の発行済みの請求書だけです")
    if invoice["payments"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "入金が登録されています。入金を取り消してから請求書を取り消してください")
    if invoice["correction_draft_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "作成中の訂正版があります。訂正版を削除してから取り消してください")
    conn.execute("UPDATE app.invoices SET status = 'void', voided_at = now() WHERE id = %s", (invoice_id,))
    sync_order_status(conn, _order_ids(conn, [invoice_id]))
    return get_invoice_record(conn, invoice_id)


@router.post("/{invoice_id}/payments", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def add_payment(invoice_id: UUID, body: PaymentIn, conn: TenantConn):
    """入金(プラス)または返金(マイナス)を登録する。入金の合計が請求額に届いたら「入金済」になる。"""
    invoice = get_invoice_record(conn, invoice_id, for_update=True)
    if invoice["status"] not in ("issued", "paid") or invoice["superseded_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "入金を登録できるのは、最新版の発行済みの請求書だけです")
    balance = invoice["balance"]
    if body.amount > 0 and body.amount > max(balance, Decimal(0)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"入金額が残額({max(balance, 0):,}円)を超えています")
    if body.amount < 0 and -body.amount > max(-balance, Decimal(0)):
        # 返金は、訂正による減額などで入金が請求額を上回っているときだけ登録できる
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"返金額が過入金の額({max(-balance, 0):,}円)を超えています")
    conn.execute(
        """
        INSERT INTO app.payments (tenant_id, invoice_id, paid_on, amount, memo, created_by)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, app.current_user_id())
        """,
        (invoice_id, body.paid_on, body.amount, body.memo),
    )
    sync_paid_status(conn, invoice_id)
    return get_invoice_record(conn, invoice_id)


@router.delete("/{invoice_id}/payments/{payment_id}", response_model=InvoiceOut)
def delete_payment(invoice_id: UUID, payment_id: UUID, conn: TenantConn):
    """誤って登録した入金を取り消す(取り消した記録は変更履歴に残る)。"""
    invoice = get_invoice_record(conn, invoice_id, for_update=True)
    if invoice["superseded_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "訂正で置き換わった旧版の入金は変更できません")
    payment = conn.execute(
        "SELECT receipt_id FROM app.payments WHERE id = %s AND invoice_id = %s", (payment_id, invoice_id)
    ).fetchone()
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "入金が見つかりません")
    if payment["receipt_id"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "まとめ入金から割り当てた入金です。「入金」の画面で、元のまとめ入金ごと取り消してください",
        )
    conn.execute("DELETE FROM app.payments WHERE id = %s", (payment_id,))
    sync_paid_status(conn, invoice_id)
    return get_invoice_record(conn, invoice_id)


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: UUID, conn: TenantConn):
    """作成中ならプレビュー(下書きの透かし入り)、発行後はその版の控えをそのまま返す。"""
    invoice = get_invoice_record(conn, invoice_id)
    if invoice["status"] == "draft":
        previous = get_invoice_record(conn, invoice["previous_version_id"]) if invoice["previous_version_id"] else None
        invoice["due_date"] = invoice["due_date"] or invoice["default_due_date"]
        return pdf_response(render_invoice(invoice, company(conn), preview=True, previous=previous), "請求書_下書き.pdf")

    doc = conn.execute(
        "SELECT storage_path FROM app.issued_documents WHERE doc_type = 'invoice' AND document_id = %s",
        (invoice_id,),
    ).fetchone()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "発行時の控えが見つかりません")
    return pdf_response(storage.read(doc["storage_path"]), f"請求書_{invoice['invoice_number']}_第{invoice['version']}版.pdf")
