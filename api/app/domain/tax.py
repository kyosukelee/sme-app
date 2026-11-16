"""消費税の計算。インボイス制度に合わせ、端数処理は1枚の書類につき税率ごとに1回だけ行う。

金額はすべて Decimal で扱う。float は 0.1 を正確に表せないため使わない。
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Literal

RoundingMode = Literal["floor", "half_up", "ceil"]

# DB の設定値 → Decimal の丸め方。
# 「切り捨て」「切り上げ」は日本の商慣習どおり 0 を基準にする(マイナスの値引き額でも対称になる)
_ROUNDING = {
    "floor": ROUND_DOWN,      # 切り捨て
    "half_up": ROUND_HALF_UP,  # 四捨五入
    "ceil": ROUND_UP,          # 切り上げ
}

YEN = Decimal("1")


def round_yen(value: Decimal, mode: RoundingMode) -> Decimal:
    return value.quantize(YEN, rounding=_ROUNDING[mode])


@dataclass(frozen=True)
class Line:
    amount: Decimal       # 税抜金額(値引きはマイナス)
    tax_category: str     # standard / reduced / exempt / non_taxable / export
    tax_rate: Decimal     # 書類の日付時点の税率(非課税・不課税・免税は 0)


@dataclass(frozen=True)
class TaxSummary:
    tax_category: str
    tax_rate: Decimal
    taxable_amount: Decimal  # 税率ごとの対価の合計(税抜)
    tax_amount: Decimal      # 税率ごとに1回だけ端数処理した消費税額


@dataclass(frozen=True)
class Totals:
    subtotal: Decimal
    tax: Decimal
    total: Decimal


def line_amount(quantity: Decimal, unit_price: Decimal, mode: RoundingMode) -> Decimal:
    """明細の金額(数量×単価)を円単位にする。

    丸め方は設計書で未決のため、ひとまず消費税と同じ設定を使う。
    """
    return round_yen(quantity * unit_price, mode)


def summarize(lines: Iterable[Line], mode: RoundingMode) -> list[TaxSummary]:
    """明細を税区分・税率ごとに集計し、税額を税率ごとに1回だけ端数処理する。"""
    taxable: dict[tuple[str, Decimal], Decimal] = defaultdict(Decimal)
    for line in lines:
        taxable[(line.tax_category, line.tax_rate)] += line.amount

    summaries = []
    for (category, rate), amount in sorted(taxable.items(), key=lambda kv: (-kv[0][1], kv[0][0])):
        summaries.append(
            TaxSummary(
                tax_category=category,
                tax_rate=rate,
                taxable_amount=amount,
                tax_amount=round_yen(amount * rate, mode),
            )
        )
    return summaries


def totals(summaries: Iterable[TaxSummary]) -> Totals:
    summaries = list(summaries)
    subtotal = sum((s.taxable_amount for s in summaries), Decimal("0"))
    tax = sum((s.tax_amount for s in summaries), Decimal("0"))
    return Totals(subtotal=subtotal, tax=tax, total=subtotal + tax)
