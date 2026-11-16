"""日付。書類の日付は日本時間で決める(サーバーの時刻は UTC のことが多いため)。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def today() -> date:
    return datetime.now(JST).date()
