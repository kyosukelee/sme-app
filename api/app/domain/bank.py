"""銀行の入出金明細(CSV)の読み取りと、振込名義の照合用の正規化。

銀行の CSV は銀行ごとに列の並び・文字コード・日付や金額の書き方が違う。
列の並びは取込形式として会社ごとに設定し、ここでは書き方の違いだけを吸収する。
"""

import csv
import io
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------
# 文字コードと行の読み取り
# ---------------------------------------------------------------------


def decode(data: bytes, encoding: str = "auto") -> tuple[str, str]:
    """CSV のバイト列を文字列にする。auto なら UTF-8 を試し、だめなら Shift_JIS(cp932)で読む。

    戻り値は (文字列, 使った文字コード)。
    """
    if encoding in ("auto", "utf-8"):
        try:
            return data.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            if encoding == "utf-8":
                raise ValueError("UTF-8 として読めません。文字コードを「Shift_JIS」にしてください") from None
    try:
        # 銀行の CSV の多くは Windows の Shift_JIS(cp932)。丸数字や機種依存文字も読めるよう cp932 を使う
        return data.decode("cp932"), "shift_jis"
    except UnicodeDecodeError:
        raise ValueError("ファイルの文字コードを判定できません。CSV ファイルか確認してください") from None


def read_rows(text: str) -> list[list[str]]:
    """CSV(まれにタブ区切り)を行のリストにする。空の行は除く。"""
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]


# ---------------------------------------------------------------------
# 日付と金額
# ---------------------------------------------------------------------

_ERA_START = {"R": 2018, "令和": 2018, "H": 1988, "平成": 1988}  # 元年 = 開始年 + 1


def parse_date(value: str) -> date | None:
    """2026/11/10、2026-11-10、20261110、2026年11月10日、令和8年11月10日、R8.11.10 などを読む。"""
    parts = unicodedata.normalize("NFKC", value).split()
    if not parts:
        return None
    s = parts[0]  # 「2026/11/10 10:15」のように時刻が付いていたら日付だけ見る
    m = re.fullmatch(r"(令和|平成|R|H)(\d{1,2}|元)[./年-](\d{1,2})[./月-](\d{1,2})日?", s, re.IGNORECASE)
    if m:
        era, y, mo, d = m.groups()
        year = _ERA_START[era.upper() if len(era) == 1 else era] + (1 if y == "元" else int(y))
        return _safe_date(year, int(mo), int(d))
    m = re.fullmatch(r"(\d{4})[./年-](\d{1,2})[./月-](\d{1,2})日?(?:\D.*)?", s)
    if m:
        return _safe_date(*map(int, m.groups()))
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return _safe_date(*map(int, m.groups()))
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_amount(value: str) -> Decimal | None:
    """1,234、¥1,234、1,234円、△1,234(マイナス)などを読む。空なら None。"""
    s = unicodedata.normalize("NFKC", value).strip()
    s = s.replace(",", "").replace("¥", "").replace("\\", "").replace("円", "").replace(" ", "")
    if not s or s in ("-", "*"):
        return None
    negative = s[0] in "△▲-" or (s.startswith("(") and s.endswith(")"))
    s = s.strip("△▲-+()")
    try:
        amount = Decimal(s)
    except InvalidOperation:
        return None
    return -amount if negative else amount


# ---------------------------------------------------------------------
# 振込名義の正規化
# ---------------------------------------------------------------------

_SMALL_KANA = str.maketrans("ァィゥェォッャュョヮヵヶ", "アイウエオツヤユヨワカケ")
_HYPHENS = str.maketrans({"-": "ー", "‐": "ー", "−": "ー", "–": "ー", "—": "ー", "―": "ー"})
# 銀行の明細で使われる法人の略語(カ)=株式会社、ユ)=有限会社、ド)=合同会社 など)。小さい仮名は大きくしてから照合する
_ENTITY = "カ|ユ|ド|メ|シ|ゴ|シヤ|ザイ|ガク|イ|フク|トク|シユウ|ソ|ギヨ|キヨウ|ノウ|セイ|ベン|ゼイ|シホウ|ギ|ケン|ドク|コウ"
_ENTITY_RE = re.compile(rf"\((?:{_ENTITY})\)|^(?:{_ENTITY})\)|\((?:{_ENTITY})$")
_DROP_RE = re.compile(r"[\s・.,、。'\"/]")


def normalize_payer(name: str) -> str:
    """振込名義を突き合わせ用にそろえる。

    半角カナ→全角、ひらがな→カタカナ、小さい仮名→大きい仮名(銀行は小さい仮名を使えない)、
    法人の略語「カ)」「(カ」などと空白・記号を取り除く。英字は大文字にそろえる。
    """
    s = unicodedata.normalize("NFKC", name).upper()
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)  # ひらがな→カタカナ
    s = s.translate(_SMALL_KANA).translate(_HYPHENS)
    s = _DROP_RE.sub("", s)
    s = re.sub(r"^\d{4,}", "", s)  # 名義の前に付く振込依頼人コード(数字)を除く
    s = _ENTITY_RE.sub("", s)
    return _ENTITY_RE.sub("", s)  # 「カ)ABC(ユ」のように前後に付いていても取る


def payer_similarity(statement_key: str, customer_key: str) -> int:
    """名義の近さ。2=一致、1=片方がもう片方を含む(4文字以上)、0=一致しない。"""
    if not statement_key or not customer_key:
        return 0
    if statement_key == customer_key:
        return 2
    shorter = min(len(statement_key), len(customer_key))
    if shorter >= 4 and (customer_key in statement_key or statement_key in customer_key):
        return 1
    return 0
