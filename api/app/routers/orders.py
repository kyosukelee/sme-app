"""受注の API。

見積からの変換と、見積なしの直接作成の両方に対応する。
未請求のあいだは明細・金額も含めて変更でき、請求済みになると変更できなくなる(DB のトリガーでも強制)。
注文請書は発行するたびに版を上げて控えを保存する。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from psycopg import Connection
from pydantic import BaseModel, Field

from app import storage
from app import activity
from app.auth import TenantConn
from app.clock import today
from app.documents import company, get_customer, pdf_response, read_lines, write_lines
from app.fields import OptionalHalfwidthStr
from app.pdf.render import render_order_acknowledgment
from app.domain.tax import Line, TaxSummary, Totals, line_amount, summarize, totals
from app.pricing import Pricing, PricedLine, price, rounding_mode, tax_rates
from app.routers.quotes import get_quote_record
from app.schemas import LineOut, OrderLineIn, TaxSummaryOut

router = APIRouter(prefix="/api/orders", tags=["orders"])

ListFilter = Literal["all", "open", "billed"]


class OrderIn(BaseModel):
    customer_id: UUID
    title: OptionalHalfwidthStr = Field(default=None, max_length=200)
    remarks: OptionalHalfwidthStr = Field(default=None, max_length=2000)
    order_date: date | None = None         # 省略すると今日
    delivery_due_date: date | None = None  # 納期
    customer_order_ref: OptionalHalfwidthStr = Field(default=None, max_length=50)  # お客様の注文番号
    lines: list[OrderLineIn] = Field(min_length=1, max_length=100)


class OrderLineOut(LineOut):
    billed: bool  # 請求書(作成中を含む)に載っている行。載っている行は変更できない
    quote_line_id: UUID | None
    delivered_on: date | None


class AcknowledgmentOut(BaseModel):
    version: int
    issued_at: datetime


class OrderListItem(BaseModel):
    id: UUID
    order_number: str
    status: Literal["open", "billed"]
    customer_name: str
    title: str | None
    order_date: date
    delivery_due_date: date | None
    total_amount: Decimal
    quote_number: str | None
    customer_order_ref: str | None


class InvoiceRef(BaseModel):
    id: UUID
    invoice_number: str | None
    version: int
    status: str


class OrderOut(OrderListItem):
    customer_id: UUID
    customer_honorific: str
    customer_address: str | None
    remarks: str | None
    subtotal_amount: Decimal
    tax_amount: Decimal
    updated_at: datetime
    quote_id: UUID | None
    source_import_id: UUID | None   # 注文書の取り込みから作った受注なら、その取り込み
    lines: list[OrderLineOut]
    tax_summaries: list[TaxSummaryOut]
    acknowledgments: list[AcknowledgmentOut]
    invoices: list[InvoiceRef]       # この受注の明細を載せた請求書(取消・旧版を除く)
    unbilled_line_count: int         # まだどの請求書にも載っていない明細の数


ORDER_COLUMNS = """
    o.id, o.tenant_id, o.order_number, o.status, o.quote_id, q.quote_number,
    o.customer_id, o.customer_name, o.customer_honorific, o.customer_address,
    o.title, o.remarks, o.order_date, o.delivery_due_date,
    o.subtotal_amount, o.tax_amount, o.total_amount, o.updated_at,
    o.customer_order_ref, o.source_import_id
"""


def get_order_record(conn: Connection, order_id: UUID, *, for_update: bool = False) -> dict:
    lock = "FOR UPDATE OF o" if for_update else ""
    order = conn.execute(
        f"SELECT {ORDER_COLUMNS} FROM app.orders o LEFT JOIN app.quotes q ON q.id = o.quote_id WHERE o.id = %s {lock}",
        (order_id,),
    ).fetchone()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "受注が見つかりません")
    order["lines"], order["tax_summaries"] = read_lines(conn, "order", order_id)
    on_invoice = {
        r["order_line_id"]
        for r in conn.execute(
            "SELECT DISTINCT order_line_id FROM app.order_line_billing WHERE order_line_id = ANY(%s::uuid[])",
            ([line["line_id"] for line in order["lines"]],),
        ).fetchall()
    }
    for line in order["lines"]:
        line["billed"] = line["line_id"] in on_invoice
    order["acknowledgments"] = conn.execute(
        """
        SELECT version, issued_at FROM app.issued_documents
        WHERE doc_type = 'order' AND document_id = %s ORDER BY version
        """,
        (order_id,),
    ).fetchall()
    order["invoices"] = conn.execute(
        """
        SELECT DISTINCT i.id, i.invoice_number, i.version, i.status, i.created_at
        FROM app.order_lines ol
        JOIN app.order_line_billing b ON b.order_line_id = ol.id
        JOIN app.invoices i ON i.id = b.invoice_id
        WHERE ol.order_id = %s ORDER BY i.created_at
        """,
        (order_id,),
    ).fetchall()
    order["unbilled_line_count"] = conn.execute(
        """
        SELECT count(*) AS n FROM app.order_lines ol
        WHERE ol.order_id = %s AND NOT EXISTS (SELECT 1 FROM app.order_line_billing b WHERE b.order_line_id = ol.id)
        """,
        (order_id,),
    ).fetchone()["n"]
    return order


def _require_not_invoiced(order: dict, action: str) -> None:
    """請求書(作成中を含む)に明細が載っている受注は、明細が入れ替わると困るので変更・削除させない。"""
    if order["invoices"]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"この受注は請求書に使われているため{action}できません")


def _require_open(order: dict, action: str) -> None:
    if order["status"] != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"請求済みの受注は{action}できません")


def _require_unique_ref(conn: Connection, customer_id: UUID, ref: str | None, order_id: UUID | None = None) -> None:
    """同じ取引先の同じ注文番号の受注は1件だけ(二重登録を防ぐ)。"""
    if not ref:
        return
    row = conn.execute(
        """
        SELECT order_number FROM app.orders
        WHERE customer_id = %s AND customer_order_ref = %s AND (%s::uuid IS NULL OR id <> %s)
        """,
        (customer_id, ref, order_id, order_id),
    ).fetchone()
    if row:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"この取引先の注文番号「{ref}」は、受注 {row['order_number']} で登録済みです"
        )


def _insert_order(conn: Connection, *, number: str, quote_id: UUID | None, customer_id: UUID, customer: dict,
                  body_title: str | None, body_remarks: str | None, order_date: date,
                  delivery_due_date: date | None, lines: list, pricing: Pricing | None = None,
                  customer_order_ref: str | None = None, source_import_id: UUID | None = None) -> UUID:
    pricing = pricing or price(conn, lines, order_date)
    order_id = conn.execute(
        """
        INSERT INTO app.orders
          (tenant_id, order_number, quote_id, customer_id, customer_name, customer_honorific, customer_address,
           title, remarks, order_date, delivery_due_date, subtotal_amount, tax_amount, total_amount, created_by,
           customer_order_ref, source_import_id)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, app.current_user_id(), %s, %s)
        RETURNING id
        """,
        (number, quote_id, customer_id, customer["name"], customer["honorific"], customer["address"],
         body_title, body_remarks, order_date, delivery_due_date,
         pricing.totals.subtotal, pricing.totals.tax, pricing.totals.total, customer_order_ref, source_import_id),
    ).fetchone()["id"]
    write_lines(conn, "order", order_id, lines, pricing)
    return order_id


@router.get("", response_model=list[OrderListItem])
def list_orders(conn: TenantConn, filter: ListFilter = "all"):
    return conn.execute(
        f"""
        SELECT {ORDER_COLUMNS} FROM app.orders o LEFT JOIN app.quotes q ON q.id = o.quote_id
        WHERE %(f)s = 'all' OR o.status = %(f)s
        ORDER BY o.order_date DESC, o.created_at DESC
        LIMIT 200
        """,
        {"f": filter},
    ).fetchall()


def create_order_record(conn: Connection, body: OrderIn, *, source_import_id: UUID | None = None) -> UUID:
    """見積なしの受注を登録する(画面からの登録と、注文書の取り込みの確定で共通)。"""
    customer = get_customer(conn, body.customer_id)
    _require_unique_ref(conn, body.customer_id, body.customer_order_ref)
    order_date = body.order_date or today()
    number = conn.execute("SELECT app.next_document_number('order', %s) AS n", (order_date,)).fetchone()["n"]
    lines = [line.model_copy(update={"quote_line_id": None}) for line in body.lines]
    return _insert_order(
        conn, number=number, quote_id=None, customer_id=body.customer_id, customer=customer,
        body_title=body.title, body_remarks=body.remarks, order_date=order_date,
        delivery_due_date=body.delivery_due_date, lines=lines,
        customer_order_ref=body.customer_order_ref, source_import_id=source_import_id,
    )


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(body: OrderIn, conn: TenantConn):
    """見積なしで受注を登録する。番号は登録時に採番する。"""
    return get_order_record(conn, create_order_record(conn, body))


@router.post("/from-quote/{quote_id}", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def convert_quote(quote_id: UUID, conn: TenantConn):
    """発行済みの見積を受注に変換する。期限切れの見積も変換できる(確認は画面で行う)。"""
    quote = get_quote_record(conn, quote_id, for_update=True)
    if quote["status"] == "ordered":
        raise HTTPException(status.HTTP_409_CONFLICT, "この見積はすでに受注に変換されています")
    if quote["status"] != "issued":
        raise HTTPException(status.HTTP_409_CONFLICT, "受注に変換できるのは発行済みの見積だけです")

    order_date = today()
    number = conn.execute("SELECT app.next_document_number('order', %s) AS n", (order_date,)).fetchone()["n"]
    lines = [OrderLineIn(**{**line, "line_id": None}, quote_line_id=line["line_id"]) for line in quote["lines"]]
    customer = {"name": quote["customer_name"], "honorific": quote["customer_honorific"],
                "address": quote["customer_address"]}
    # 金額は計算し直さず、お客様に提示した見積の金額をそのまま引き継ぐ
    # (変換までに端数処理の設定や税率が変わっても、受注額が見積額とずれないようにする)
    quoted = Pricing(
        lines=[PricedLine(amount=l["amount"], tax_rate=l["tax_rate"]) for l in quote["lines"]],
        summaries=[TaxSummary(**s) for s in quote["tax_summaries"]],
        totals=Totals(subtotal=quote["subtotal_amount"], tax=quote["tax_amount"], total=quote["total_amount"]),
    )
    order_id = _insert_order(
        conn, number=number, quote_id=quote_id, customer_id=quote["customer_id"], customer=customer,
        body_title=quote["title"], body_remarks=quote["remarks"], order_date=order_date,
        delivery_due_date=None, lines=lines, pricing=quoted,
    )
    conn.execute("UPDATE app.quotes SET status = 'ordered' WHERE id = %s", (quote_id,))
    return get_order_record(conn, order_id)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: UUID, conn: TenantConn):
    return get_order_record(conn, order_id)


@router.put("/{order_id}", response_model=OrderOut)
def update_order(order_id: UUID, body: OrderIn, conn: TenantConn):
    """受注を変更する。請求書に載った行は固定し、それ以外の行は追加・変更・削除できる。

    明細は行ID(line_id)で突き合わせて行ごとに更新する。月ごとに分けて請求する受注で、
    請求が始まったあとも残りの行の納品日を入力できるようにするため。
    """
    order = get_order_record(conn, order_id, for_update=True)
    _require_open(order, "変更")

    existing = {line["line_id"]: line for line in order["lines"]}
    locked = {line_id for line_id, line in existing.items() if line["billed"]}
    incoming_ids = [line.line_id for line in body.lines if line.line_id]

    if len(incoming_ids) != len(set(incoming_ids)) or any(i not in existing for i in incoming_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "明細の行を特定できません。画面を読み込み直してください")
    if locked - set(incoming_ids):
        raise HTTPException(status.HTTP_409_CONFLICT, "請求書に載っている行は削除できません")
    if locked and body.customer_id != order["customer_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "請求書がある受注の取引先は変更できません")
    for no, line in enumerate(body.lines, start=1):
        if line.line_id in locked:
            old = existing[line.line_id]
            same = (line.description == old["description"] and line.quantity == old["quantity"]
                    and (line.unit or None) == old["unit"] and line.unit_price == old["unit_price"]
                    and line.tax_category == old["tax_category"] and line.delivered_on == old["delivered_on"])
            if not same:
                raise HTTPException(status.HTTP_409_CONFLICT, f"{no}行目は請求書に載っているため変更できません")

    customer = get_customer(conn, body.customer_id)
    _require_unique_ref(conn, body.customer_id, body.customer_order_ref, order_id)
    order_date = body.order_date or order["order_date"]
    mode = rounding_mode(conn)
    rates = tax_rates(conn, order_date)

    # 請求書に載った行は保存済みの金額をそのまま使い、それ以外の行だけ計算し直す
    priced = []
    for line in body.lines:
        if line.line_id in locked:
            priced.append((existing[line.line_id]["amount"], existing[line.line_id]["tax_rate"]))
        else:
            priced.append((line_amount(line.quantity, line.unit_price, mode), rates[line.tax_category]))
    summaries = summarize(
        (Line(amount, line.tax_category, rate) for line, (amount, rate) in zip(body.lines, priced, strict=True)), mode
    )
    result = totals(summaries)

    conn.execute(
        """
        UPDATE app.orders
        SET customer_id = %s, customer_name = %s, customer_honorific = %s, customer_address = %s,
            title = %s, remarks = %s, order_date = %s, delivery_due_date = %s,
            subtotal_amount = %s, tax_amount = %s, total_amount = %s, customer_order_ref = %s
        WHERE id = %s
        """,
        (body.customer_id, customer["name"], customer["honorific"], customer["address"], body.title,
         body.remarks, order_date, body.delivery_due_date, result.subtotal, result.tax, result.total,
         body.customer_order_ref, order_id),
    )

    # 画面から消された行を削除し、残りを行ごとに更新・追加する。
    # 行番号の重複を避けるため、いったんマイナスにしてから振り直す
    conn.execute(
        "DELETE FROM app.order_lines WHERE order_id = %s AND NOT (id = ANY(%s::uuid[]))", (order_id, incoming_ids)
    )
    conn.execute("UPDATE app.order_lines SET line_no = -line_no WHERE order_id = %s", (order_id,))
    for no, (line, (amount, rate)) in enumerate(zip(body.lines, priced, strict=True), start=1):
        if line.line_id:
            conn.execute(
                """
                UPDATE app.order_lines
                SET line_no = %s, description = %s, quantity = %s, unit = %s, unit_price = %s,
                    amount = %s, tax_category = %s, tax_rate = %s, delivered_on = %s
                WHERE id = %s
                """,
                (no, line.description, line.quantity, line.unit or None, line.unit_price,
                 amount, line.tax_category, rate, line.delivered_on, line.line_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO app.order_lines
                  (tenant_id, order_id, line_no, description, quantity, unit, unit_price, amount,
                   tax_category, tax_rate, quote_line_id, delivered_on)
                VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (order_id, no, line.description, line.quantity, line.unit or None, line.unit_price,
                 amount, line.tax_category, rate, line.quote_line_id, line.delivered_on),
            )

    conn.execute("DELETE FROM app.order_tax_summaries WHERE order_id = %s", (order_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO app.order_tax_summaries (tenant_id, order_id, tax_category, tax_rate, taxable_amount, tax_amount)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s)
            """,
            [(order_id, s.tax_category, s.tax_rate, s.taxable_amount, s.tax_amount) for s in summaries],
        )
    return get_order_record(conn, order_id)


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(order_id: UUID, conn: TenantConn):
    """未請求の受注を削除する。見積から変換した受注なら、見積を発行済みに戻す。"""
    order = get_order_record(conn, order_id, for_update=True)
    _require_open(order, "削除")
    _require_not_invoiced(order, "削除")
    if order["acknowledgments"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "注文請書を発行した受注は削除できません")
    conn.execute("DELETE FROM app.orders WHERE id = %s", (order_id,))
    if order["quote_id"]:
        conn.execute("UPDATE app.quotes SET status = 'issued' WHERE id = %s", (order["quote_id"],))


@router.post("/{order_id}/acknowledgment", response_model=OrderOut)
def issue_acknowledgment(order_id: UUID, conn: TenantConn):
    """注文請書を発行する。いまの内容で PDF を作り、版を上げて控えを保存する。"""
    order = get_order_record(conn, order_id, for_update=True)
    issue_date = today()
    version = len(order["acknowledgments"]) + 1
    pdf = render_order_acknowledgment(order, company(conn), issue_date=issue_date, preview=False)
    key = f"{order['tenant_id']}/orders/{order['order_number']}_ack_v{version}_{uuid4().hex[:8]}.pdf"
    sha256 = storage.save(key, pdf)
    conn.execute(
        """
        INSERT INTO app.issued_documents
          (tenant_id, doc_type, document_id, document_number, version, storage_path, sha256,
           transaction_date, total_amount, customer_name, issued_by)
        VALUES (app.current_tenant_id(), 'order', %s, %s, %s, %s, %s, %s, %s, %s, app.current_user_id())
        """,
        (order_id, order["order_number"], version, key, sha256, issue_date,
         order["total_amount"], order["customer_name"]),
    )
    return get_order_record(conn, order_id)


@router.get("/{order_id}/acknowledgment/pdf")
def acknowledgment_pdf(order_id: UUID, conn: TenantConn, preview: bool = False, version: int | None = None):
    """preview=true ならいまの内容のプレビュー(控えは保存しない)。それ以外は保存した控え(既定は最新版)。"""
    order = get_order_record(conn, order_id)
    if preview:
        pdf = render_order_acknowledgment(order, company(conn), issue_date=today(), preview=True)
        return pdf_response(pdf, f"注文請書_{order['order_number']}_プレビュー.pdf")

    doc = conn.execute(
        """
        SELECT storage_path, version FROM app.issued_documents
        WHERE doc_type = 'order' AND document_id = %s AND (%s::int IS NULL OR version = %s)
        ORDER BY version DESC LIMIT 1
        """,
        (order_id, version, version),
    ).fetchone()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "注文請書はまだ発行されていません")
    activity.record(conn, "open_document", f"注文請書 {order['order_number']} 第{doc['version']}版", "order", order_id)
    return pdf_response(storage.read(doc["storage_path"]), f"注文請書_{order['order_number']}_第{doc['version']}版.pdf")
