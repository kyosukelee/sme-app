"""書類の金額計算。会社の端数処理の設定と、書類の日付時点の税率を DB から読み、
domain/tax.py で計算する。見積・受注・請求で共通に使う。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from psycopg import Connection

from app.domain.tax import Line, RoundingMode, TaxSummary, Totals, line_amount, summarize, totals


class LineValues(Protocol):
    quantity: Decimal
    unit_price: Decimal
    tax_category: str


@dataclass(frozen=True)
class PricedLine:
    amount: Decimal
    tax_rate: Decimal


@dataclass(frozen=True)
class Pricing:
    lines: list[PricedLine]
    summaries: list[TaxSummary]
    totals: Totals


def rounding_mode(conn: Connection) -> RoundingMode:
    row = conn.execute("SELECT rounding_mode FROM app.tenant_settings").fetchone()
    if row is None:
        raise RuntimeError("会社の設定(tenant_settings)がありません")
    return row["rounding_mode"]


def is_tax_exempt(conn: Connection) -> bool:
    """自社が免税事業者か(自社情報が未登録なら課税事業者として扱う)。"""
    row = conn.execute("SELECT tax_exempt FROM app.company_profiles").fetchone()
    return bool(row and row["tax_exempt"])


def tax_rates(conn: Connection, on_date: date) -> dict[str, Decimal]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (code) code, rate
        FROM app.tax_categories
        WHERE valid_from <= %s
        ORDER BY code, valid_from DESC
        """,
        (on_date,),
    ).fetchall()
    return {r["code"]: r["rate"] for r in rows}


def price(conn: Connection, lines: Sequence[LineValues], on_date: date) -> Pricing:
    """明細金額(数量×単価)と税率別の集計を計算する。

    明細金額の円未満は、消費税と同じ会社設定の端数処理で丸める。
    税率は on_date 時点のものを使う。ただし明細が取引日(transaction_date)を持つ場合(請求書)は、
    消費税の決まりに合わせて明細ごとの取引日時点の税率を使う。
    免税事業者は消費税を請求しないので、税率はすべて 0 として計算する(入力した金額をそのまま請求する)。
    """
    mode = rounding_mode(conn)
    exempt = is_tax_exempt(conn)
    rates_by_date: dict[date, dict[str, Decimal]] = {}

    def rate_for(line: LineValues) -> Decimal:
        if exempt:
            return Decimal("0")
        d = getattr(line, "transaction_date", None) or on_date
        if d not in rates_by_date:
            rates_by_date[d] = tax_rates(conn, d)
        return rates_by_date[d][line.tax_category]

    priced = [
        PricedLine(amount=line_amount(line.quantity, line.unit_price, mode), tax_rate=rate_for(line))
        for line in lines
    ]
    summaries = summarize(
        (Line(p.amount, line.tax_category, p.tax_rate) for line, p in zip(lines, priced, strict=True)),
        mode,
    )
    return Pricing(lines=priced, summaries=summaries, totals=totals(summaries))
