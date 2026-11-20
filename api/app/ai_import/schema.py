"""AI に返させる値の形(構造化出力)。

AI には書類に書かれていることだけを書き写させる。数量・金額は文字列のまま受け取り、
数値への変換・取引先や商品の割り当て・検証はコード(domain/import_checks.py)で行う。
"""

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["order", "quote_request", "other"]


class Classification(BaseModel):
    """書類の種類の判定。"""

    doc_type: DocType = Field(
        description="order=注文書・発注書、quote_request=見積依頼書、other=それ以外(請求書・納品書・見積書など)"
    )
    reason: str = Field(default="", description="そう判断した理由を短く(例: 表題が「注文書」)")


class ExtractedLine(BaseModel):
    item_code: str | None = Field(default=None, description="品番・商品コード。書かれていなければ null")
    description: str | None = Field(default=None, description="品名・内容(書かれているとおり)")
    quantity: str | None = Field(default=None, description="数量(数字だけ。例: 12、2.5)")
    unit: str | None = Field(default=None, description="単位(個・式・時間など)")
    unit_price: str | None = Field(default=None, description="単価(書かれているとおり。例: 1,200)")
    amount: str | None = Field(default=None, description="明細の金額(書かれているとおり)")
    tax_rate: Literal["10", "8", "0"] | None = Field(
        default=None, description="この行の消費税率。軽減税率の印(※など)があれば 8。書かれていなければ null"
    )
    page: int | None = Field(default=None, description="この行が書かれているページ番号(1から)")


class ExtractedOrder(BaseModel):
    """注文書から読み取った内容。読めない・書かれていない項目は null にする(推測で埋めない)。"""

    issuer_name: str | None = Field(default=None, description="注文書を出した会社(注文主)の名前。書かれているとおり")
    issuer_address: str | None = Field(default=None, description="注文主の住所")
    issuer_phone: str | None = Field(default=None, description="注文主の電話番号")
    recipient_name: str | None = Field(default=None, description="宛先(注文を受ける会社)の名前。「御中」は除く")
    customer_order_ref: str | None = Field(default=None, description="注文書の番号(注文番号・発注番号・No.)")
    order_date: str | None = Field(default=None, description="注文日・発行日。YYYY-MM-DD に直す(和暦は西暦に)")
    delivery_due_date: str | None = Field(default=None, description="納期・納入期日。YYYY-MM-DD に直す(和暦は西暦に)")
    title: str | None = Field(default=None, description="件名")
    remarks: str | None = Field(default=None, description="備考・特記事項(書かれているとおり)")
    prices_include_tax: bool | None = Field(
        default=None, description="単価が税込みと明記されていれば true、税抜きと明記されていれば false、不明なら null"
    )
    lines: list[ExtractedLine] = Field(default_factory=list, description="明細の行(書かれている順)")
    subtotal: str | None = Field(default=None, description="小計(税抜合計)")
    tax_total: str | None = Field(default=None, description="消費税額の合計")
    total: str | None = Field(default=None, description="合計金額(税込)")
    uncertain_fields: list[str] = Field(
        default_factory=list,
        description="読み取りに自信がない項目の名前(例: order_date、lines[2].quantity)",
    )
