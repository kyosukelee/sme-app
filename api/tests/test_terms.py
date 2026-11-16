"""締め日・支払期限の計算のテスト。DB なしで動く。"""

from datetime import date

import pytest

from app.domain.terms import closing_date_on_or_after, due_date, latest_closing_date, previous_closing_date

END = 31  # 月末


@pytest.mark.parametrize(
    ("d", "closing_day", "expected"),
    [
        (date(2026, 11, 12), END, date(2026, 11, 30)),  # 月末締め
        (date(2026, 11, 30), END, date(2026, 11, 30)),  # 締め日当日
        (date(2026, 11, 12), 20, date(2026, 11, 20)),   # 20日締め
        (date(2026, 11, 21), 20, date(2026, 12, 20)),   # 20日締めの翌期間
        (date(2026, 12, 25), 20, date(2027, 1, 20)),    # 年をまたぐ
        (date(2027, 2, 10), END, date(2027, 2, 28)),    # 2月の月末
        (date(2028, 2, 10), END, date(2028, 2, 29)),    # うるう年
        (date(2027, 2, 15), 30, date(2027, 2, 28)),     # 30日締めでも2月は月末
    ],
)
def test_締め日(d, closing_day, expected):
    assert closing_date_on_or_after(d, closing_day) == expected


def test_前回の締め日():
    assert previous_closing_date(date(2026, 11, 30), END) == date(2026, 10, 31)
    assert previous_closing_date(date(2027, 1, 20), 20) == date(2026, 12, 20)
    assert previous_closing_date(date(2027, 3, 31), END) == date(2027, 2, 28)


def test_直近の締め日():
    assert latest_closing_date(date(2026, 11, 12), END) == date(2026, 10, 31)
    assert latest_closing_date(date(2026, 11, 30), END) == date(2026, 11, 30)
    assert latest_closing_date(date(2026, 11, 25), 20) == date(2026, 11, 20)


@pytest.mark.parametrize(
    ("closing", "offset", "payment_day", "expected"),
    [
        (date(2026, 11, 30), 1, END, date(2026, 12, 31)),  # 月末締め翌月末払い
        (date(2026, 11, 20), 1, 10, date(2026, 12, 10)),   # 20日締め翌月10日払い
        (date(2026, 12, 31), 1, END, date(2027, 1, 31)),   # 年をまたぐ
        (date(2027, 1, 31), 1, END, date(2027, 2, 28)),    # 翌月末が2月
        (date(2026, 11, 30), 2, 10, date(2027, 1, 10)),    # 翌々月10日払い
        (date(2026, 11, 30), 0, END, date(2026, 11, 30)),  # 当月末払い
    ],
)
def test_支払期限(closing, offset, payment_day, expected):
    assert due_date(closing, offset, payment_day) == expected
