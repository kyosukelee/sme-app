"""会社名の正規化と、似ている度合いの計算(注文書の発行元と取引先の照合に使う)。

注文書の会社名は、同じ会社でも書き方がばらばらになる。
  ・法人格の書き方: 株式会社 / (株) / ㈱ / カブシキガイシャ、前株と後株
  ・全角と半角、空白や「・」の有無
  ・敬称や部署: 御中、様、営業部 など
これらをそろえてから比べる。銀行の振込名義(カナ)の照合は domain/bank.py が受け持つ。
"""

import re
import unicodedata

# 長いものから順に取り除く(「一般社団法人」を「社団法人」より先に)
_ENTITIES = sorted(
    [
        "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
        "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人", "社団法人", "財団法人",
        "特定非営利活動法人", "NPO法人", "医療法人社団", "医療法人財団", "医療法人",
        "社会福祉法人", "学校法人", "宗教法人", "協同組合",
        "(株)", "(有)", "(同)", "(合)", "(資)", "(名)", "(社)", "(財)", "(医)", "(福)", "(学)",
    ],
    key=len,
    reverse=True,
)
_HONORIFICS = re.compile(r"(御中|様|殿|各位)$")
_DROP = re.compile(r"[\s・.,、。'\"/\-‐ー_]")


def normalize_company(name: str | None) -> str:
    """照合用にそろえた会社名を返す(表示には使わない)。"""
    if not name:
        return ""
    # NFKC で全角英数字・全角括弧を半角に、㈱ を (株) にそろえる
    text = unicodedata.normalize("NFKC", name).strip()
    text = _HONORIFICS.sub("", text).strip()
    for entity in _ENTITIES:
        text = text.replace(entity, "")
    text = _DROP.sub("", text)
    return text.lower()


def _bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}


def name_similarity(a: str, b: str) -> int:
    """正規化した2つの会社名の近さ(0〜100)。完全一致 100、片方がもう片方を含む 80、それ以外は2文字ずつの重なり。"""
    if not a or not b:
        return 0
    if a == b:
        return 100
    if a in b or b in a:
        return 80
    x, y = _bigrams(a), _bigrams(b)
    return round(60 * 2 * len(x & y) / (len(x) + len(y)))

