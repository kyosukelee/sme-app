"""源泉徴収税額の計算のテスト。DB なしで動く。"""

from decimal import Decimal as D

import pytest

from app.domain.withholding import withholding_tax


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("100000", "10210"),      # 10万円 × 10.21%
        ("55555", "5672"),        # 5,672.1655 → 切り捨て
        ("1000000", "102100"),    # ちょうど100万円
        ("1000001", "102100"),    # 超えた1円分: 0.2042円 → 切り捨てで0
        ("1500000", "204200"),    # 102,100 + 50万円 × 20.42%
        ("0", "0"),
        ("-5000", "0"),           # 値引きで小計がマイナスでも源泉はかけない
    ],
)
def test_源泉徴収税額(base, expected):
    assert withholding_tax(D(base)) == D(expected)
