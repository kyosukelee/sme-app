"""見積・受注に共通の処理(取引先のコピー、明細の書き込み、自社情報、PDF の返し方)。"""

from typing import Literal
from urllib.parse import quote as url_quote
from uuid import UUID

from fastapi import HTTPException, Response, status
from psycopg import Connection

from app.pricing import Pricing

Kind = Literal["quote", "order", "invoice"]

# 書類ごとに明細が追加で持つ項目
_EXTRA_COLUMNS: dict[str, list[str]] = {
    "quote": [],
    "order": ["quote_line_id", "delivered_on"],
    "invoice": ["order_line_id", "transaction_date", "date_confirmed"],
}


def get_customer(conn: Connection, customer_id: UUID) -> dict:
    customer = conn.execute(
        "SELECT name, honorific, address FROM app.customers WHERE id = %s AND is_active",
        (customer_id,),
    ).fetchone()
    if customer is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "取引先が見つかりません")
    return customer


def read_lines(conn: Connection, kind: Kind, doc_id: UUID) -> tuple[list[dict], list[dict]]:
    """明細と税率別の集計を読む。"""
    extra = "".join(f", {c}" for c in _EXTRA_COLUMNS[kind])
    lines = conn.execute(
        f"""
        SELECT id AS line_id, line_no, description, quantity, unit, unit_price, amount, tax_category, tax_rate{extra}
        FROM app.{kind}_lines WHERE {kind}_id = %s ORDER BY line_no
        """,
        (doc_id,),
    ).fetchall()
    summaries = conn.execute(
        f"""
        SELECT tax_category, tax_rate, taxable_amount, tax_amount
        FROM app.{kind}_tax_summaries WHERE {kind}_id = %s ORDER BY tax_rate DESC, tax_category
        """,
        (doc_id,),
    ).fetchall()
    return lines, summaries


def write_lines(conn: Connection, kind: Kind, doc_id: UUID, lines: list, pricing: Pricing) -> None:
    """明細と税率別の集計をまとめて入れ替える。"""
    conn.execute(f"DELETE FROM app.{kind}_lines WHERE {kind}_id = %s", (doc_id,))
    conn.execute(f"DELETE FROM app.{kind}_tax_summaries WHERE {kind}_id = %s", (doc_id,))

    extra = _EXTRA_COLUMNS[kind]
    extra_cols = "".join(f", {c}" for c in extra)
    extra_vals = ", %s" * len(extra)
    rows = [
        [doc_id, no, line.description, line.quantity, line.unit or None, line.unit_price,
         p.amount, line.tax_category, p.tax_rate, *(getattr(line, c) for c in extra)]
        for no, (line, p) in enumerate(zip(lines, pricing.lines, strict=True), start=1)
    ]

    with conn.cursor() as cur:
        cur.executemany(
            f"""
            INSERT INTO app.{kind}_lines
              (tenant_id, {kind}_id, line_no, description, quantity, unit, unit_price, amount, tax_category, tax_rate{extra_cols})
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s, %s{extra_vals})
            """,
            rows,
        )
        cur.executemany(
            f"""
            INSERT INTO app.{kind}_tax_summaries
              (tenant_id, {kind}_id, tax_category, tax_rate, taxable_amount, tax_amount)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s)
            """,
            [(doc_id, s.tax_category, s.tax_rate, s.taxable_amount, s.tax_amount) for s in pricing.summaries],
        )


def company(conn: Connection) -> dict | None:
    return conn.execute(
        """
        SELECT business_type, name, representative_name, tax_exempt,
               postal_code, address, phone, registration_number, bank_account
        FROM app.company_profiles
        """
    ).fetchone()


def pdf_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{url_quote(filename)}"},
    )
