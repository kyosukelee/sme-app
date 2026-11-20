"""注文書の取り込み: AI の読み取り結果を受注の下書きにし、検証する(AI を使わない普通のコード)。

・AI の値は文字列のまま受け取り、ここで数値・日付に直す
・取引先・商品は候補を出すだけで、取引先は自動で決めない
・検証はエラー(直すまで確定できない)と警告(確認すれば確定できる)に分ける
・金額は自社の計算(会社設定の端数処理と税率)で決め、注文書の金額は照合にだけ使う
"""

import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.domain.bank import parse_amount, parse_date
from app.domain.names import name_similarity, normalize_company

TAX_BY_RATE = {"10": "standard", "8": "reduced"}
RATE_LABEL = {"standard": "10%", "reduced": "8%(軽減)"}

MAX_LINES = 100
MAX_DESCRIPTION = 200
MAX_REF = 50

_QUANTITY = re.compile(r"^\d{1,9}(\.\d{1,3})?$")
_PRICE = re.compile(r"^-?\d{1,13}(\.\d{1,2})?$")

FIELD_LABELS = {
    "issuer_name": "注文主",
    "recipient_name": "宛先",
    "customer_order_ref": "注文番号",
    "order_date": "注文日",
    "delivery_due_date": "納期",
    "title": "件名",
    "remarks": "備考",
    "subtotal": "小計",
    "tax_total": "消費税",
    "total": "合計",
}
LINE_LABELS = {
    "item_code": "品番",
    "description": "品名",
    "quantity": "数量",
    "unit": "単位",
    "unit_price": "単価",
    "amount": "金額",
    "tax_rate": "税率",
}


# ---------------------------------------------------------------------
# 文字列 → 数値・日付
# ---------------------------------------------------------------------
def clean_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip() if value is not None else ""


def to_number_text(value: Any) -> str:
    """「1,200円」「¥1,200」「１２００」などを「1200」にする。読めなければ元の文字(空白は詰める)。"""
    text = clean_text(value)
    if not text:
        return ""
    amount = parse_amount(text)
    if amount is None:
        return re.sub(r"\s", "", text)
    return format(amount, "f")


def to_decimal(value: Any) -> Decimal | None:
    text = to_number_text(value)
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def to_date_text(value: Any) -> str:
    """YYYY-MM-DD にする。読めなければ空。"""
    text = clean_text(value)
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        parsed = parse_date(text)
        return parsed.isoformat() if parsed else ""


def _fmt(amount: Decimal | None) -> str:
    return "―" if amount is None else f"{amount:,.0f}円"


# ---------------------------------------------------------------------
# 商品・取引先の照合
# ---------------------------------------------------------------------
def match_item(code: Any, description: Any, items: list[dict]) -> dict | None:
    """商品コードの完全一致 → 正規化した品名の完全一致 の順に探す。見つからなければ None。"""
    code_text = clean_text(code).lower()
    if code_text:
        for item in items:
            if item.get("code") and clean_text(item["code"]).lower() == code_text:
                return item
    name = normalize_company(clean_text(description))
    if name:
        for item in items:
            if normalize_company(item["name"]) == name:
                return item
    return None


def customer_candidates(issuer_name: Any, issuer_address: Any, customers: list[dict], limit: int = 3) -> list[dict]:
    """注文主の名前・住所から、取引先の候補を近い順に返す(30点未満は出さない)。"""
    raw = clean_text(issuer_name)
    key = normalize_company(raw)
    address = normalize_company(clean_text(issuer_address))
    results = []
    for c in customers:
        reasons: list[str] = []
        score = 0
        registered = {normalize_company(n) for n in c.get("document_names") or []}
        if key and key in registered:
            score, reasons = 100, ["注文書上の名前として登録済み"]
        else:
            sim = name_similarity(key, normalize_company(c["name"]))
            if sim == 100:
                reasons.append("名前が一致")
            elif sim >= 80:
                reasons.append("名前の一部が一致")
            elif sim >= 30:
                reasons.append("名前が似ている")
            score = sim
        if address and c.get("address") and name_similarity(address, normalize_company(c["address"])) >= 50:
            score += 10
            reasons.append("住所が近い")
        if score >= 30:
            results.append({"customer_id": str(c["id"]), "name": c["name"], "score": min(score, 100), "reasons": reasons})
    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results[:limit]


# ---------------------------------------------------------------------
# 下書き
# ---------------------------------------------------------------------
def initial_draft(order: dict, items: list[dict]) -> dict:
    """AI の読み取り結果(ExtractedOrder を dict にしたもの)から、確認画面の初期値を作る。"""
    lines = []
    for raw in order.get("lines") or []:
        item = match_item(raw.get("item_code"), raw.get("description"), items)
        doc_rate = raw.get("tax_rate")
        if doc_rate in TAX_BY_RATE:
            tax_category = TAX_BY_RATE[doc_rate]
        elif item:
            tax_category = item["tax_category"]
        else:
            tax_category = "standard"
        lines.append({
            "description": clean_text(raw.get("description")),
            "quantity": to_number_text(raw.get("quantity")),
            "unit": clean_text(raw.get("unit")),
            "unit_price": to_number_text(raw.get("unit_price")),
            "tax_category": tax_category,
            "item_id": str(item["id"]) if item else None,
            "item_code": clean_text(raw.get("item_code")) or None,
            "doc_amount": to_number_text(raw.get("amount")) or None,
            "doc_tax_rate": doc_rate,
            "page": raw.get("page"),
        })
    return {
        "customer_id": None,
        "customer_order_ref": clean_text(order.get("customer_order_ref")),
        "order_date": to_date_text(order.get("order_date")),
        "delivery_due_date": to_date_text(order.get("delivery_due_date")),
        "title": clean_text(order.get("title")),
        "remarks": clean_text(order.get("remarks")),
        "lines": lines,
    }


def line_problem(line: dict) -> bool:
    """品名・数量・単価のどれかが受注に登録できない形か。"""
    description = (line.get("description") or "").strip()
    quantity = to_number_text(line.get("quantity"))
    price = to_number_text(line.get("unit_price"))
    return (
        not description
        or len(description) > MAX_DESCRIPTION
        or not _QUANTITY.match(quantity)
        or Decimal(quantity) <= 0
        or not _PRICE.match(price)
    )


def is_blank_line(line: dict) -> bool:
    return not any((line.get(k) or "").strip() for k in ("description", "unit_price", "unit"))


# ---------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------
@dataclass
class Check:
    id: str                              # 警告の確認に使う(同じ問題なら同じ id)
    level: Literal["error", "warning"]
    message: str
    field: str | None = None             # 画面で移る先(例: customer_id、lines[2])


@dataclass
class Context:
    order: dict                           # AI の読み取り結果(ExtractedOrder)
    company_name: str | None
    duplicate_order_number: str | None    # 同じ取引先・同じ注文番号の受注があればその受注番号
    priced: list[Decimal | None]          # 下書きの明細ごとの自社計算の金額(計算できない行は None)
    totals: dict[str, Decimal] | None     # 自社計算の小計・税額・合計(全行が計算できたときだけ)
    items: dict[str, dict]                # 商品ID → 商品
    tax_exempt: bool
    today: date


def validate(draft: dict, ctx: Context) -> list[Check]:
    checks: list[Check] = []
    err = lambda i, m, f=None: checks.append(Check(i, "error", m, f))  # noqa: E731
    warn = lambda i, m, f=None: checks.append(Check(i, "warning", m, f))  # noqa: E731
    order = ctx.order
    lines = [l for l in draft.get("lines") or [] if not is_blank_line(l)]

    # ---- エラー ----
    if not draft.get("customer_id"):
        err("customer_missing", "取引先を選んでください。", "customer_id")
    if not lines:
        err("no_lines", "明細がありません。", "lines")
    if len(lines) > MAX_LINES:
        err("too_many_lines", f"明細は{MAX_LINES}行までです。", "lines")
    for i, line in enumerate(draft.get("lines") or []):
        if not is_blank_line(line) and line_problem(line):
            err(f"line_invalid:{i}", f"{i + 1}行目の品名・数量・単価を確認してください。", f"lines[{i}]")
    ref = (draft.get("customer_order_ref") or "").strip()
    if len(ref) > MAX_REF:
        err("ref_too_long", f"注文番号は{MAX_REF}文字までです。", "customer_order_ref")
    if ctx.duplicate_order_number:
        err(
            "duplicate_ref",
            f"この取引先の注文番号「{ref}」は、受注 {ctx.duplicate_order_number} で登録済みです。"
            "別の注文なら注文番号を直してください。",
            "customer_order_ref",
        )

    # ---- 警告 ----
    if not draft.get("order_date"):
        warn("order_date_missing", "注文日が空欄です。このまま確定すると今日の日付になります。", "order_date")
    else:
        od = date.fromisoformat(draft["order_date"])
        if od > ctx.today:
            warn("order_date_future", "注文日が今日より後になっています。", "order_date")
        elif od < ctx.today - timedelta(days=365):
            warn("order_date_old", "注文日が1年以上前です。", "order_date")
        if draft.get("delivery_due_date") and date.fromisoformat(draft["delivery_due_date"]) < od:
            warn("due_before_order", "納期が注文日より前になっています。", "delivery_due_date")

    recipient = clean_text(order.get("recipient_name"))
    if recipient and ctx.company_name and name_similarity(normalize_company(recipient), normalize_company(ctx.company_name)) < 80:
        warn("recipient_mismatch", f"宛先「{recipient}」が自社名「{ctx.company_name}」と一致しません。他社宛ての書類ではないか確認してください。", None)

    if order.get("prices_include_tax"):
        warn("prices_include_tax", "注文書の単価は税込みと書かれています。受注の単価は税抜きなので、税抜きの単価に直してください。", "lines")

    for i, line in enumerate(draft.get("lines") or []):
        if is_blank_line(line):
            continue
        ours = ctx.priced[i] if i < len(ctx.priced) else None
        theirs = to_decimal(line.get("doc_amount"))
        if ours is not None and theirs is not None and ours != theirs:
            warn(f"line_amount:{i}", f"{i + 1}行目: 注文書の金額 {_fmt(theirs)} と、数量×単価 {_fmt(ours)} が違います。", f"lines[{i}]")
        item = ctx.items.get(line.get("item_id") or "")
        if not ctx.tax_exempt and item and line.get("doc_tax_rate") in TAX_BY_RATE and TAX_BY_RATE[line["doc_tax_rate"]] != item["tax_category"]:
            warn(
                f"tax_vs_item:{i}",
                f"{i + 1}行目: 注文書の税率({line['doc_tax_rate']}%)と、商品マスタの「{item['name']}」の税率"
                f"({RATE_LABEL.get(item['tax_category'], item['tax_category'])})が違います。",
                f"lines[{i}]",
            )

    if not ctx.tax_exempt and lines and all(not l.get("doc_tax_rate") and not l.get("item_id") for l in lines):
        warn("tax_assumed", "明細ごとの税率が書かれていないため、10%にしています。軽減税率(8%)の品目がないか確認してください。", "lines")

    if ctx.totals is not None:
        for key, label in (("subtotal", "小計"), ("tax_total", "消費税"), ("total", "合計")):
            theirs = to_decimal(order.get(key))
            ours = ctx.totals["tax" if key == "tax_total" else key]
            if theirs is not None and theirs != ours:
                diff = ours - theirs
                warn(
                    f"total_mismatch:{key}",
                    f"{label}: 注文書は {_fmt(theirs)}、自社の計算は {_fmt(ours)}(差額 {diff:+,.0f}円)です。"
                    "端数処理の違いか、明細の読み取りの誤りがないか確認してください。",
                    "totals",
                )

    uncertain = [_uncertain_label(f) for f in order.get("uncertain_fields") or []]
    uncertain = [u for u in uncertain if u]
    if uncertain:
        warn("ai_uncertain", "AIが読み取りに自信がないとした項目があります: " + "、".join(uncertain), None)
    return checks


def _uncertain_label(name: str) -> str | None:
    m = re.fullmatch(r"lines\[(\d+)\]\.(\w+)", name.strip())
    if m:
        # AI には行を1から数えるよう指示している
        return f"{int(m.group(1))}行目の{LINE_LABELS.get(m.group(2), m.group(2))}"
    return FIELD_LABELS.get(name.strip())


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]


# ---------------------------------------------------------------------
# AI の値と確定した値の比較(修正の記録)
# ---------------------------------------------------------------------
def corrections(order: dict, final: dict) -> list[tuple[str, str | None, str | None]]:
    """AI が読んだ値と、人が確定した値が違う項目を (項目, AIの値, 確定値) で返す。"""
    out: list[tuple[str, str | None, str | None]] = []

    def cmp(name: str, ai: str, fin: str) -> None:
        if (ai or "") != (fin or ""):
            out.append((name, ai or None, fin or None))

    cmp("customer_order_ref", clean_text(order.get("customer_order_ref")), (final.get("customer_order_ref") or "").strip())
    cmp("order_date", to_date_text(order.get("order_date")), final.get("order_date") or "")
    cmp("delivery_due_date", to_date_text(order.get("delivery_due_date")), final.get("delivery_due_date") or "")
    cmp("title", clean_text(order.get("title")), (final.get("title") or "").strip())
    cmp("remarks", clean_text(order.get("remarks")), (final.get("remarks") or "").strip())

    ai_lines = order.get("lines") or []
    fin_lines = [l for l in final.get("lines") or [] if not is_blank_line(l)]
    if len(ai_lines) != len(fin_lines):
        out.append(("lines.count", str(len(ai_lines)), str(len(fin_lines))))
    for i, (a, f) in enumerate(zip(ai_lines, fin_lines)):
        cmp(f"lines[{i + 1}].description", clean_text(a.get("description")), (f.get("description") or "").strip())
        cmp(f"lines[{i + 1}].quantity", to_number_text(a.get("quantity")), to_number_text(f.get("quantity")))
        cmp(f"lines[{i + 1}].unit", clean_text(a.get("unit")), (f.get("unit") or "").strip())
        cmp(f"lines[{i + 1}].unit_price", to_number_text(a.get("unit_price")), to_number_text(f.get("unit_price")))
        if a.get("tax_rate") in TAX_BY_RATE:
            cmp(f"lines[{i + 1}].tax_category", TAX_BY_RATE[a["tax_rate"]], f.get("tax_category") or "")
    return out
