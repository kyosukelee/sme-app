"""入力文字の正規化。

全角の英字・数字と、番号・住所・メールアドレスに使う記号を半角にそろえる。
画面のフォント(BIZ UDPゴシック)では全角数字が細く表示されて見分けにくいが、
PDF では全角の幅で印字されるため、保存の時点でそろえておく。

次のものは日本語の文章として全角で書くのが普通なので変えない。
  ・括弧(()、句読点、コロン(:)、感嘆符・疑問符(!?)
  ・全角スペース(字下げに使われる)
  ・丸数字(①)、カタカナの長音(ー)
"""

# 半角にする全角記号: ＠ ＃ ％ ＆ ＋ － ． ／ ＿ ＝
_SYMBOLS = "＠＃％＆＋－．／＿＝"

_TABLE = {
    **{code: code - 0xFEE0 for code in range(ord("０"), ord("９") + 1)},
    **{code: code - 0xFEE0 for code in range(ord("Ａ"), ord("Ｚ") + 1)},
    **{code: code - 0xFEE0 for code in range(ord("ａ"), ord("ｚ") + 1)},
    **{ord(c): ord(c) - 0xFEE0 for c in _SYMBOLS},
}


def to_halfwidth(value: str) -> str:
    """全角の英数字と一部の記号を半角にする。"""
    return value.translate(_TABLE)


def to_halfwidth_optional(value: str | None) -> str | None:
    return None if value is None else to_halfwidth(value)
