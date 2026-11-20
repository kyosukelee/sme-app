"""書類の PDF 生成。共通の HTML テンプレート(templates/document.html)を WeasyPrint で PDF にする。"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

TEMPLATE_DIR = Path(__file__).parent / "templates"

TAX_LABELS = {
    "standard": "10%対象",
    "reduced": "8%対象(軽減税率)",
    "exempt": "非課税",
    "non_taxable": "不課税",
    "export": "免税",
}


def yen(value: Decimal | int) -> str:
    return f"{Decimal(value):,.0f}"


def jdate(value: date | None) -> str:
    return f"{value.year}年{value.month}月{value.day}日" if value else ""


def sdate(value: date | None) -> str:
    return f"{value.year}/{value.month:02d}/{value.day:02d}" if value else ""


def qty(value: Decimal) -> str:
    """数量・単価の表示。桁区切りを付け、小数部の不要な0を落とす(1.500 → 1.5、150000.00 → 150,000)。"""
    text = f"{Decimal(value):,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html"]))
_env.filters.update(yen=yen, jdate=jdate, sdate=sdate, qty=qty, tax_label=lambda c: TAX_LABELS.get(c, c))


def _render(doc: dict, company: dict | None) -> bytes:
    company = company or {}
    # 免税事業者は消費税を表示しない(請求書は発行時の写し、見積・注文請書はいまの自社情報で決まる)
    doc.setdefault("exempt", bool(company.get("tax_exempt")))
    html = _env.get_template("document.html").render(doc=doc, company=company)
    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def _common(record: dict) -> dict:
    """見積・受注に共通の項目(宛先・明細・金額・備考)。"""
    return {
        "customer_name": record["customer_name"],
        "customer_honorific": record["customer_honorific"],
        "customer_address": record["customer_address"],
        "subject": record["title"],
        "lines": record["lines"],
        "tax_summaries": record["tax_summaries"],
        "subtotal_amount": record["subtotal_amount"],
        "tax_amount": record["tax_amount"],
        "total_amount": record["total_amount"],
        "remarks": record["remarks"],
    }


def render_quote(quote: dict, company: dict | None, *, draft: bool) -> bytes:
    """見積書。draft=True なら「下書き」の透かしを入れる(発行前のプレビュー用)。"""
    return _render(
        {
            **_common(quote),
            "title": "御見積書",
            "number": quote["quote_number"],
            "watermark": "下書き" if draft else None,
            "intro": ["下記のとおりお見積り申し上げます。"],
            "meta": [
                ("見積番号", quote["quote_number"] or "(発行時に採番)"),
                ("発行日", jdate(quote["issue_date"]) or "(未発行)"),
                ("有効期限", jdate(quote["valid_until"]) or "(発行日から設定日数)"),
            ],
            "amount_label": "お見積金額",
        },
        company,
    )


def render_order_acknowledgment(order: dict, company: dict | None, *, issue_date: date, preview: bool) -> bytes:
    """注文請書。preview=True なら「プレビュー」の透かしを入れる(控えは保存しない)。"""
    meta = [("受注番号", order["order_number"]), ("受注日", jdate(order["order_date"]))]
    if order["delivery_due_date"]:
        meta.append(("納期", jdate(order["delivery_due_date"])))
    if order.get("customer_order_ref"):
        meta.append(("貴社注文番号", order["customer_order_ref"]))
    if order.get("quote_number"):
        meta.append(("見積番号", order["quote_number"]))
    meta.append(("発行日", jdate(issue_date)))
    return _render(
        {
            **_common(order),
            "title": "注文請書",
            "number": order["order_number"],
            "watermark": "プレビュー" if preview else None,
            "intro": ["このたびはご注文いただき、誠にありがとうございます。", "下記のとおりご注文をお請けいたします。"],
            "meta": meta,
            "amount_label": "ご注文金額",
        },
        company,
    )


def render_invoice(invoice: dict, company: dict | None, *, preview: bool, previous: dict | None = None) -> bytes:
    """請求書。発行者名と登録番号は、発行時に書類へコピーした値を使う(作成中はいまの自社情報)。

    previous には訂正版のときの元の版(請求番号・請求日)を渡す。
    """
    issuer = dict(company or {})
    if invoice["status"] != "draft":
        issuer["name"] = invoice["issuer_name"]
        issuer["registration_number"] = invoice["issuer_registration_number"]
        issuer["representative_name"] = invoice["issuer_representative"]
        issuer["tax_exempt"] = invoice["issuer_tax_exempt"]
    withholding_amount = invoice.get("withholding_amount") or 0
    net = invoice["total_amount"] - withholding_amount

    dates = {line["transaction_date"] for line in invoice["lines"]}
    meta = [("請求番号", invoice["invoice_number"] or "(発行時に採番)")]
    if invoice["version"] > 1:
        meta.append(("版", f"第{invoice['version']}版(訂正版)"))
    meta.append(("請求日", jdate(invoice["issue_date"]) or "(発行日)"))
    meta.append(("お支払期限", jdate(invoice["due_date"]) or "―"))
    if len(dates) == 1:
        meta.append(("取引日", jdate(next(iter(dates)))))

    notices = []
    if previous:
        notices.append(
            f"本書は{jdate(previous['issue_date'])}発行の請求書(第{previous['version']}版)を訂正したものです。"
        )

    return _render(
        {
            **_common(invoice),
            "title": "請求書",
            "number": invoice["invoice_number"],
            "watermark": "下書き" if preview else None,
            "intro": ["下記のとおりご請求申し上げます。"],
            "notices": notices,
            "meta": meta,
            "amount_label": "ご請求金額",
            "date_column": len(dates) > 1,
            "bank_account": issuer.get("bank_account"),
            "exempt": bool(issuer.get("tax_exempt")),
            # 源泉徴収する請求書は、源泉徴収税額と差引ご請求額を印字する
            "withholding": {"amount": withholding_amount, "net": net} if invoice.get("withholding") else None,
            # 締め請求で未入金が残っているときだけ、合計請求書の形式で繰越を印字する
            "carryover": (
                {
                    "amount": invoice["carried_over_amount"],
                    "amount_due": invoice["carried_over_amount"] + net,
                    "entries": invoice["carryovers"],
                }
                if invoice.get("carried_over_amount")
                else None
            ),
        },
        issuer,
    )
