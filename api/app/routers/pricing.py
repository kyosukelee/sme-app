"""入力途中の明細の金額計算。画面はこの結果を表示し、自分では計算しない。"""

from fastapi import APIRouter

from app.auth import TenantConn
from app.clock import today
from app.domain.withholding import withholding_tax
from app.pricing import price
from app.schemas import CalculateIn, CalculateOut, PricedLineOut, TaxSummaryOut

router = APIRouter(prefix="/api", tags=["pricing"])


@router.post("/calculate", response_model=CalculateOut)
def calculate(body: CalculateIn, conn: TenantConn):
    pricing = price(conn, body.lines, body.on_date or today())
    withheld = withholding_tax(pricing.totals.subtotal) if body.withholding else 0
    return CalculateOut(
        lines=[PricedLineOut(amount=p.amount, tax_rate=p.tax_rate) for p in pricing.lines],
        tax_summaries=[TaxSummaryOut(**vars(s)) for s in pricing.summaries],
        subtotal_amount=pricing.totals.subtotal,
        tax_amount=pricing.totals.tax,
        total_amount=pricing.totals.total,
        withholding_amount=withheld,
        net_amount=pricing.totals.total - withheld,
    )
