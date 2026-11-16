"""源泉徴収税額の計算(報酬・料金等)。

個人事業主が、デザイン料・原稿料・講演料・コンサルタント料などを法人などに請求するとき、
支払う側が所得税と復興特別所得税を差し引く(源泉徴収)。
  ・100万円以下の部分: 10.21%
  ・100万円を超える部分: 20.42%(100万円までの分は 102,100円)
  ・1円未満の端数は切り捨て

対象額は、消費税額が請求書で区分して表示されていれば税抜金額でよい(国税庁)。
このアプリの請求書は税率ごとに消費税額を区分して印字するので、税抜の小計を対象にする。
免税事業者は消費税を表示しないので、請求額そのもの(= 小計)が対象になる。
"""

from decimal import ROUND_DOWN, Decimal

THRESHOLD = Decimal("1000000")
RATE_LOW = Decimal("0.1021")
RATE_HIGH = Decimal("0.2042")


def withholding_tax(base: Decimal) -> Decimal:
    """源泉徴収税額(円未満切り捨て)。base は源泉徴収の対象額(税抜の小計)。"""
    if base <= 0:
        return Decimal("0")
    if base <= THRESHOLD:
        amount = base * RATE_LOW
    else:
        amount = THRESHOLD * RATE_LOW + (base - THRESHOLD) * RATE_HIGH
    return amount.quantize(Decimal("1"), rounding=ROUND_DOWN)
