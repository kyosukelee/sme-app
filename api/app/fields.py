"""API の入力で使う文字列の型。保存する前に全角の英数字を半角にそろえる。"""

from typing import Annotated

from pydantic import AfterValidator

from app.domain.text import to_halfwidth, to_halfwidth_optional

HalfwidthStr = Annotated[str, AfterValidator(to_halfwidth)]
OptionalHalfwidthStr = Annotated[str | None, AfterValidator(to_halfwidth_optional)]
