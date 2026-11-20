"""注文書の取り込み: 照合と検証の決まり(DB を使わない)。"""

from datetime import date
from decimal import Decimal

from app.domain import import_checks as ic
from app.domain.names import name_similarity, normalize_company


def test_会社名の正規化():
    assert normalize_company("(株)瀬戸内物産") == "瀬戸内物産"
    assert normalize_company("㈱瀬戸内物産 御中") == "瀬戸内物産"
    assert normalize_company("株式会社 瀬戸内物産") == "瀬戸内物産"
    assert normalize_company("瀬戸内物産株式会社") == "瀬戸内物産"
    assert normalize_company("ＡＢＣ商会") == "abc商会"
    assert normalize_company("一般社団法人 ひろしま観光協会") == "ひろしま観光協会"


def test_会社名の近さ():
    assert name_similarity("瀬戸内物産", "瀬戸内物産") == 100
    assert name_similarity("瀬戸内物産", "瀬戸内物産呉支店") == 80
    assert 0 < name_similarity("瀬戸内物産", "瀬戸内商事") < 80
    assert name_similarity("", "瀬戸内物産") == 0


def test_数字と日付の読み替え():
    assert ic.to_number_text("1,200円") == "1200"
    assert ic.to_number_text("１２００") == "1200"
    assert ic.to_number_text("¥18.5") == "18.5"
    assert ic.to_number_text(None) == ""
    assert ic.to_date_text("2026-11-20") == "2026-11-20"
    assert ic.to_date_text("令和8年11月20日") == "2026-11-20"
    assert ic.to_date_text("12月上旬") == ""


CUSTOMERS = [
    {"id": "c1", "name": "株式会社瀬戸内物産", "address": "広島県呉市3-3-3", "document_names": []},
    {"id": "c2", "name": "宮島観光サービス", "address": "広島県廿日市市4-4-4", "document_names": ["宮島観光(株) 経理課"]},
    {"id": "c3", "name": "福山精機株式会社", "address": None, "document_names": []},
]


def test_取引先の候補():
    found = ic.customer_candidates("(株)瀬戸内物産", "広島県呉市3-3-3", CUSTOMERS)
    assert found[0]["customer_id"] == "c1"
    assert found[0]["score"] == 100
    assert found[0]["reasons"] == ["名前が一致", "住所が近い"]
    assert all(c["customer_id"] != "c3" for c in found)  # 似ていない取引先は出さない

    # 前に確定したときの「注文書上の名前」は登録済みとして最優先
    learned = ic.customer_candidates("宮島観光(株) 経理課", None, CUSTOMERS)
    assert learned[0]["customer_id"] == "c2"
    assert learned[0]["reasons"] == ["注文書上の名前として登録済み"]

    assert ic.customer_candidates(None, None, CUSTOMERS) == []


ITEMS = [
    {"id": "i1", "code": "PPR-A4", "name": "A4コピー用紙", "unit": "箱", "unit_price": Decimal("600"), "tax_category": "standard"},
    {"id": "i2", "code": None, "name": "会議用お茶 500ml", "unit": "本", "unit_price": Decimal("120"), "tax_category": "reduced"},
]


def order(**over):
    base = {
        "issuer_name": "(株)瀬戸内物産",
        "recipient_name": "株式会社サンプル商事",
        "customer_order_ref": "SN-1",
        "order_date": "2026-11-20",
        "delivery_due_date": "2026-12-10",
        "title": "事務用品",
        "remarks": None,
        "prices_include_tax": False,
        "lines": [
            {"item_code": "PPR-A4", "description": "A4コピー用紙(500枚入)", "quantity": "20", "unit": "箱",
             "unit_price": "600", "amount": "12,000", "tax_rate": "10", "page": 1},
            {"item_code": None, "description": "会議用お茶 500ml", "quantity": "48", "unit": "本",
             "unit_price": "120", "amount": "5,760", "tax_rate": None, "page": 1},
        ],
        "subtotal": "17,760",
        "tax_total": "1,660",
        "total": "19,420",
        "uncertain_fields": [],
    }
    return {**base, **over}


def test_下書きの初期値():
    draft = ic.initial_draft(order(), ITEMS)
    assert draft["customer_id"] is None  # 取引先は自動で決めない
    assert draft["order_date"] == "2026-11-20"
    first, second = draft["lines"]
    assert first["item_id"] == "i1"            # 品番で一致
    assert first["quantity"] == "20" and first["doc_amount"] == "12000"
    assert second["item_id"] == "i2"           # 品名で一致
    assert second["tax_category"] == "reduced"  # 注文書に税率がなければ商品マスタの税率


def context(o, draft, **over):
    base = dict(
        order=o,
        company_name="株式会社サンプル商事",
        duplicate_order_number=None,
        priced=[Decimal("12000"), Decimal("5760")],
        totals={"subtotal": Decimal("17760"), "tax": Decimal("1660"), "total": Decimal("19420")},
        items={i["id"]: i for i in ITEMS},
        tax_exempt=False,
        today=date(2026, 11, 21),
    )
    return ic.Context(**{**base, **over})


def ids(checks, level=None):
    return {c.id for c in checks if level is None or c.level == level}


def test_取引先を選べばエラーはない():
    o = order()
    draft = ic.initial_draft(o, ITEMS)
    assert ids(ic.validate(draft, context(o, draft))) == {"customer_missing"}
    draft["customer_id"] = "c1"
    assert ic.validate(draft, context(o, draft)) == []


def test_エラーになるもの():
    o = order()
    draft = {**ic.initial_draft(o, ITEMS), "customer_id": "c1"}
    draft["lines"][0]["quantity"] = "0"
    draft["lines"][1]["unit_price"] = "百二十"
    checks = ic.validate(draft, context(o, draft, duplicate_order_number="ORD-2026-0001"))
    assert ids(checks, "error") == {"line_invalid:0", "line_invalid:1", "duplicate_ref"}

    empty = {**draft, "lines": [{"description": "", "quantity": "1", "unit": "", "unit_price": ""}]}
    assert "no_lines" in ids(ic.validate(empty, context(o, empty)), "error")


def test_金額と税率と日付の警告():
    o = order(recipient_name="株式会社よそ商店", prices_include_tax=True, uncertain_fields=["lines[2].quantity", "order_date"])
    draft = {**ic.initial_draft(o, ITEMS), "customer_id": "c1", "delivery_due_date": "2026-11-01"}
    draft["lines"][0]["doc_tax_rate"] = "8"  # 注文書は8%、商品マスタは10%
    checks = ic.validate(
        draft,
        context(o, draft, priced=[Decimal("12000"), Decimal("5700")],
                totals={"subtotal": Decimal("17700"), "tax": Decimal("1656"), "total": Decimal("19356")}),
    )
    assert ids(checks, "warning") == {
        "recipient_mismatch", "prices_include_tax", "line_amount:1", "tax_vs_item:0", "due_before_order",
        "total_mismatch:subtotal", "total_mismatch:tax_total", "total_mismatch:total", "ai_uncertain",
    }
    uncertain = next(c for c in checks if c.id == "ai_uncertain")
    assert "2行目の数量" in uncertain.message and "注文日" in uncertain.message
    assert ids(checks, "error") == set()


def test_税率が書かれていなければ確認を促す():
    lines = [{**l, "tax_rate": None, "item_code": None, "description": f"品目{i}"} for i, l in enumerate(order()["lines"])]
    o = order(lines=lines)
    draft = {**ic.initial_draft(o, ITEMS), "customer_id": "c1"}
    assert "tax_assumed" in ids(ic.validate(draft, context(o, draft, totals=None)))
    # 免税事業者は税率を使わない
    assert "tax_assumed" not in ids(ic.validate(draft, context(o, draft, totals=None, tax_exempt=True)))


def test_AIの値と確定値の違いを記録する():
    o = order()
    draft = {**ic.initial_draft(o, ITEMS), "customer_id": "c1"}
    assert ic.corrections(o, draft) == []
    draft["lines"][0]["unit_price"] = "650"
    draft["title"] = "事務用品(追加)"
    draft["lines"].append({"description": "送料", "quantity": "1", "unit": "式", "unit_price": "500", "tax_category": "standard"})
    assert ic.corrections(o, draft) == [
        ("title", "事務用品", "事務用品(追加)"),
        ("lines.count", "2", "3"),
        ("lines[1].unit_price", "600", "650"),
    ]
