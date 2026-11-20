"""取り込みの下書きの検証(DB を読む部分)。検証の決まりそのものは domain/import_checks.py にある。"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from psycopg import Connection

from app.clock import today
from app.domain import import_checks as ic
from app.pricing import is_tax_exempt, price


def active_items(conn: Connection) -> list[dict]:
    return conn.execute("SELECT id, code, name, unit, unit_price, tax_category FROM app.items WHERE is_active").fetchall()


def active_customers(conn: Connection) -> list[dict]:
    return conn.execute("SELECT id, name, address, document_names FROM app.customers WHERE is_active").fetchall()


def customer_candidates(conn: Connection, order: dict) -> list[dict]:
    return ic.customer_candidates(order.get("issuer_name"), order.get("issuer_address"), active_customers(conn))


def initial_draft(conn: Connection, order: dict) -> dict:
    draft = ic.initial_draft(order, active_items(conn))
    # 注文書上の名前が登録済み(前に確定したことがある)取引先は、最初から選んでおく
    best = customer_candidates(conn, order)
    if best and "注文書上の名前として登録済み" in best[0]["reasons"]:
        draft["customer_id"] = best[0]["customer_id"]
    return draft


def evaluate(conn: Connection, order: dict, draft: dict) -> list[dict]:
    """下書きを検証し、エラー・警告の一覧を返す。"""
    lines = draft.get("lines") or []
    order_date = date.fromisoformat(draft["order_date"]) if draft.get("order_date") else today()

    computable = [i for i, l in enumerate(lines) if not ic.is_blank_line(l) and not ic.line_problem(l)]
    priced: list[Decimal | None] = [None] * len(lines)
    totals = None
    if computable:
        values = [
            SimpleNamespace(
                quantity=Decimal(ic.to_number_text(lines[i]["quantity"])),
                unit_price=Decimal(ic.to_number_text(lines[i]["unit_price"])),
                tax_category=lines[i].get("tax_category") or "standard",
            )
            for i in computable
        ]
        pricing = price(conn, values, order_date)
        for i, p in zip(computable, pricing.lines, strict=True):
            priced[i] = p.amount
        filled = [i for i, l in enumerate(lines) if not ic.is_blank_line(l)]
        if filled == computable:
            t = pricing.totals
            totals = {"subtotal": t.subtotal, "tax": t.tax, "total": t.total}

    duplicate = None
    ref = (draft.get("customer_order_ref") or "").strip()
    if ref and draft.get("customer_id"):
        row = conn.execute(
            "SELECT order_number FROM app.orders WHERE customer_id = %s AND customer_order_ref = %s",
            (UUID(draft["customer_id"]), ref),
        ).fetchone()
        duplicate = row["order_number"] if row else None

    company = conn.execute("SELECT name FROM app.company_profiles").fetchone()
    ctx = ic.Context(
        order=order,
        company_name=company["name"] if company else None,
        duplicate_order_number=duplicate,
        priced=priced,
        totals=totals,
        items={str(i["id"]): i for i in active_items(conn)},
        tax_exempt=is_tax_exempt(conn),
        today=today(),
    )
    return ic.as_dicts(ic.validate(draft, ctx))
