"""締め日と支払期限の計算。

締め日・支払日は 1〜31 で持ち、その月にない日(31日がない月など)は月末として扱う。
31 を「月末」として設定に保存している。
"""

import calendar
from datetime import date


def _day_in_month(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + offset
    return index // 12, index % 12 + 1


def closing_date_on_or_after(d: date, closing_day: int) -> date:
    """d を含む締め期間の締め日(d 当日が締め日ならその日)。"""
    this_month = _day_in_month(d.year, d.month, closing_day)
    if d <= this_month:
        return this_month
    y, m = _shift_month(d.year, d.month, 1)
    return _day_in_month(y, m, closing_day)


def previous_closing_date(closing: date, closing_day: int) -> date:
    """1つ前の締め日。締め期間は (前回の締め日, 今回の締め日] になる。"""
    y, m = _shift_month(closing.year, closing.month, -1)
    return _day_in_month(y, m, closing_day)


def latest_closing_date(today: date, closing_day: int) -> date:
    """今日までに来た直近の締め日(締め請求の初期値に使う)。"""
    this_month = _day_in_month(today.year, today.month, closing_day)
    return this_month if this_month <= today else previous_closing_date(this_month, closing_day)


def due_date(closing: date, month_offset: int, payment_day: int) -> date:
    """締め日から支払期限を決める(例: 月末締め・翌月末払い → month_offset=1, payment_day=31)。"""
    y, m = _shift_month(closing.year, closing.month, month_offset)
    return _day_in_month(y, m, payment_day)
