"""消費税計算のテスト。DB なしで動く。"""

from decimal import Decimal as D

import pytest

from app.domain.tax import Line, line_amount, summarize, totals


def test_税率ごとに1回だけ端数処理する():
    # 明細ごとに丸めると 10%: 10+10=20円、8%: 9+9=18円 になるが、
    # インボイスでは税率ごとの合計に対して1回だけ丸める
    lines = [
        Line(D("105"), "standard", D("0.10")),
        Line(D("105"), "standard", D("0.10")),
        Line(D("119"), "reduced", D("0.08")),
        Line(D("119"), "reduced", D("0.08")),
    ]
    summaries = summarize(lines, "floor")

    assert [(s.tax_category, s.taxable_amount, s.tax_amount) for s in summaries] == [
        ("standard", D("210"), D("21")),
        ("reduced", D("238"), D("19")),
    ]
    assert totals(summaries).subtotal == D("448")
    assert totals(summaries).tax == D("40")
    assert totals(summaries).total == D("488")


@pytest.mark.parametrize(
    ("amount", "mode", "expected"),
    [
        ("1234", "floor", "123"),    # 123.4
        ("1234", "half_up", "123"),
        ("1234", "ceil", "124"),
        ("1235", "floor", "123"),    # 123.5
        ("1235", "half_up", "124"),
        ("1235", "ceil", "124"),
    ],
)
def test_端数処理の3方式(amount, mode, expected):
    [summary] = summarize([Line(D(amount), "standard", D("0.10"))], mode)
    assert summary.tax_amount == D(expected)


def test_値引き行は同じ税率の合計から差し引く():
    lines = [
        Line(D("10000"), "standard", D("0.10")),
        Line(D("-1000"), "standard", D("0.10")),
    ]
    [summary] = summarize(lines, "floor")
    assert summary.taxable_amount == D("9000")
    assert summary.tax_amount == D("900")


def test_非課税_不課税_免税は消費税額に含めない():
    lines = [
        Line(D("1000"), "standard", D("0.10")),
        Line(D("5000"), "exempt", D("0")),
        Line(D("3000"), "non_taxable", D("0")),
        Line(D("2000"), "export", D("0")),
    ]
    summaries = summarize(lines, "floor")
    result = totals(summaries)

    assert result.subtotal == D("11000")
    assert result.tax == D("100")
    assert all(s.tax_amount == 0 for s in summaries if s.tax_category != "standard")


def test_マイナスの金額も0を基準に丸める():
    # -123.5 円: 切り捨ては -123、切り上げは -124(絶対値で考える)
    [floor] = summarize([Line(D("-1235"), "standard", D("0.10"))], "floor")
    [ceil] = summarize([Line(D("-1235"), "standard", D("0.10"))], "ceil")
    assert floor.tax_amount == D("-123")
    assert ceil.tax_amount == D("-124")


def test_明細金額は数量と単価から円単位にする():
    assert line_amount(D("3"), D("333.33"), "floor") == D("999")
    assert line_amount(D("3"), D("333.33"), "half_up") == D("1000")
    assert line_amount(D("1.5"), D("1000"), "floor") == D("1500")


# ---------------------------------------------------------------------
# 入力文字の正規化(app/domain/text.py)
# ---------------------------------------------------------------------
from app.domain.text import to_halfwidth  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("２０２６年１２月分", "2026年12月分"),
        ("ＡＢＣ商事", "ABC商事"),
        ("みどり市中央１－２－３", "みどり市中央1-2-3"),
        ("ｉｎｆｏ＠ｅｘａｍｐｌｅ．ｊｐ", "info@example.jp"),
        ("保守（月額）", "保守（月額）"),         # 括弧は全角のまま
        ("　字下げ", "　字下げ"),                 # 全角スペースはそのまま
        ("①初期費用", "①初期費用"),             # 丸数字はそのまま
        ("サーバー", "サーバー"),                 # 長音はそのまま
        ("ご確認ください！", "ご確認ください！"),  # 感嘆符はそのまま
        ("担当：ＩＴ課", "担当：IT課"),           # コロンは全角のまま
    ],
)
def test_全角の英数字だけを半角にする(raw, expected):
    assert to_halfwidth(raw) == expected
