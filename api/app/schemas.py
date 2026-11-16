"""見積・受注・請求で共通に使う API の入出力の型。"""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.fields import HalfwidthStr, OptionalHalfwidthStr

TaxCategory = Literal["standard", "reduced", "exempt", "non_taxable", "export"]


class LineValues(BaseModel):
    """金額の計算に必要な項目だけの明細(入力途中の計算に使う)。"""

    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit_price: Decimal = Field(max_digits=15, decimal_places=2)  # 値引きはマイナスの単価
    tax_category: TaxCategory = "standard"


class LineIn(LineValues):
    description: HalfwidthStr = Field(min_length=1, max_length=200)
    unit: OptionalHalfwidthStr = Field(default=None, max_length=20)


class OrderLineIn(LineIn):
    line_id: UUID | None = None        # 既にある行を更新するときの行ID(新しい行は省略)
    quote_line_id: UUID | None = None  # 見積から変換した行なら元の見積明細
    delivered_on: date | None = None   # 納品日(請求書の取引日になる)


class InvoiceLineIn(LineIn):
    order_line_id: UUID | None = None  # 受注から作った行なら元の受注明細
    transaction_date: date             # 取引日(インボイスの記載事項)
    date_confirmed: bool = True        # False は受注から作ったときの仮の取引日(確認するまで発行できない)


class TaxSummaryOut(BaseModel):
    tax_category: TaxCategory
    tax_rate: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


class LineOut(BaseModel):
    line_id: UUID
    line_no: int
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    amount: Decimal
    tax_category: TaxCategory
    tax_rate: Decimal


class CalculateIn(BaseModel):
    lines: list[LineValues] = Field(max_length=100)
    on_date: date | None = None  # 税率を決める日付。省略すると今日
    withholding: bool = False    # 源泉徴収税額も計算する(請求書)


class PricedLineOut(BaseModel):
    amount: Decimal
    tax_rate: Decimal


class CalculateOut(BaseModel):
    lines: list[PricedLineOut]
    tax_summaries: list[TaxSummaryOut]
    subtotal_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    withholding_amount: Decimal = Decimal(0)  # 源泉徴収税額
    net_amount: Decimal | None = None         # 差引ご請求額(合計 − 源泉徴収税額)
