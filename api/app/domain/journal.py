"""仕訳の組み立てと、会計ソフトごとの CSV への変換。

仕訳は会計ソフトに依存しない形(勘定の種類と税区分の種類)で組み立て、出力するときに
会計ソフトの勘定科目名・税区分名(勘定奉行はコード)に置き換える。

金額はすべて税込。売上は税率ごとに1行にし、税額は請求書に載せた税率ごとの消費税額をそのまま使う
(会計ソフト側で計算し直すと、端数処理の違いで請求書と1円ずれることがあるため)。
"""

import csv
import io
from decimal import Decimal
from typing import Literal

from app.clock import today

Software = Literal["kanjo_bugyo", "yayoi", "freee", "mf"]
SOFTWARE_NAMES: dict[str, str] = {
    "kanjo_bugyo": "勘定奉行",
    "yayoi": "弥生会計",
    "freee": "freee会計",
    "mf": "マネーフォワード クラウド会計",
}

# 勘定の種類
ACCOUNT_KEYS = ("receivable", "sales", "bank", "fee", "withholding")
ACCOUNT_LABELS = {
    "receivable": "売掛金",
    "sales": "売上",
    "bank": "入金口座",
    "fee": "振込手数料",
    "withholding": "源泉徴収税額",
}

# 税区分の種類
TAX_KEYS = (
    "sales_standard", "sales_reduced", "sales_exempt", "sales_non_taxable", "sales_export",
    "out_of_scope", "purchase_standard",
)
TAX_LABELS = {
    "sales_standard": "課税売上 10%",
    "sales_reduced": "課税売上 軽減8%",
    "sales_exempt": "非課税売上",
    "sales_non_taxable": "不課税(対象外)の売上",
    "sales_export": "輸出売上(免税)",
    "out_of_scope": "対象外(売掛金・預金など)",
    "purchase_standard": "課税仕入 10%(振込手数料)",
}

# 会計ソフトごとの税区分の初期値。会計ソフトの設定(税区分の名前)に合わせて画面から変えられる。
# 勘定奉行は名前ではなくコードで受け入れ、コードは製品の版によって違うため、初期値を入れない
DEFAULT_TAX_LABELS: dict[str, dict[str, str]] = {
    "yayoi": {
        "sales_standard": "課税売上込10%", "sales_reduced": "課税売上込軽減8%", "sales_exempt": "非課売上",
        "sales_non_taxable": "対象外", "sales_export": "輸出売上", "out_of_scope": "対象外",
        "purchase_standard": "課対仕入込10%",
    },
    "mf": {
        "sales_standard": "課税売上 10%", "sales_reduced": "課税売上 (軽)8%", "sales_exempt": "非課税売上",
        "sales_non_taxable": "対象外売上", "sales_export": "輸売 0%", "out_of_scope": "対象外",
        "purchase_standard": "課税仕入 10%",
    },
    "freee": {
        "sales_standard": "課税売上10%", "sales_reduced": "課税売上8%（軽）", "sales_exempt": "非課税売上",
        "sales_non_taxable": "対象外", "sales_export": "輸出売上", "out_of_scope": "対象外",
        "purchase_standard": "課対仕入10%",
    },
    "kanjo_bugyo": {key: "" for key in TAX_KEYS},
}

# 書類の税区分 → 仕訳の税区分の種類
SALES_TAX_KEY = {
    "standard": "sales_standard", "reduced": "sales_reduced", "exempt": "sales_exempt",
    "non_taxable": "sales_non_taxable", "export": "sales_export",
}


def default_accounts(sole_proprietor: bool) -> dict[str, dict[str, str]]:
    return {
        "receivable": {"name": "売掛金", "code": "", "sub": ""},
        "sales": {"name": "売上高", "code": "", "sub": ""},
        "bank": {"name": "普通預金", "code": "", "sub": ""},
        "fee": {"name": "支払手数料", "code": "", "sub": ""},
        # 源泉徴収された所得税は、法人なら仮払税金、個人事業主なら事業主貸にするのが一般的
        "withholding": {"name": "事業主貸" if sole_proprietor else "仮払税金", "code": "", "sub": ""},
    }


# ---------------------------------------------------------------------
# 仕訳の形
#   entry = {"date", "summary", "partner", "lines": [{"debit": side, "credit": side}]}
#   side  = {"account": 勘定の種類, "tax": 税区分の種類, "amount": 税込金額, "tax_amount": 消費税額}
# ---------------------------------------------------------------------


def side(account: str, amount: Decimal, tax: str = "out_of_scope", tax_amount: Decimal = Decimal(0)) -> dict:
    return {"account": account, "tax": tax, "amount": str(amount), "tax_amount": str(tax_amount)}


def reverse_lines(lines: list[dict]) -> list[dict]:
    """逆仕訳(借方と貸方を入れ替える)。"""
    return [{"debit": line["credit"], "credit": line["debit"]} for line in lines]


# ---------------------------------------------------------------------
# 会計ソフトの CSV への変換
# ---------------------------------------------------------------------
class SettingsProblem(ValueError):
    """設定が足りず CSV を作れない(勘定奉行のコードが空など)。"""


def _yen(value: str | Decimal) -> str:
    return str(int(Decimal(value)))


def _tax_amount(s: dict) -> str:
    return _yen(s["tax_amount"]) if Decimal(s["tax_amount"]) else ""


def render(software: str, entries: list[dict], settings: dict) -> tuple[bytes, str, int]:
    """仕訳を会計ソフトの取り込み形式の CSV にする。(内容, ファイル名, 文字を置き換えた数) を返す。

    文字コードは4つとも Shift_JIS(Windows の会計ソフトが前提とするもの)。
    Shift_JIS にない文字は「?」に置き換え、その数を返す。
    """
    accounts, labels = settings["accounts"], settings["tax_labels"][software]
    customer_as_sub = settings["customer_as_sub"]

    def account(s: dict, partner: str | None) -> tuple[str, str, str]:
        """(勘定科目名, 補助科目名, コード)"""
        a = accounts[s["account"]]
        sub = a.get("sub", "")
        if s["account"] == "receivable" and customer_as_sub and partner:
            sub = partner
        return a["name"], sub, a.get("code", "")

    if software == "kanjo_bugyo":
        missing = [ACCOUNT_LABELS[k] for k in ACCOUNT_KEYS if not accounts[k].get("code")]
        missing += [TAX_LABELS[k] for k in TAX_KEYS if not labels.get(k)]
        if missing:
            raise SettingsProblem(
                "勘定奉行は勘定科目と消費税区分をコードで受け入れます。仕訳の設定で、次のコードを入力してください: "
                + "、".join(missing)
            )

    header, rows = _ROWS[software](entries, account, labels)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    if header:
        writer.writerow(header)
    writer.writerows(rows)
    text = buf.getvalue()
    data = text.encode("cp932", errors="replace")
    replaced = sum(1 for ch in text if ch.encode("cp932", errors="replace") == b"?" and ch != "?")
    stamp = today().strftime("%Y%m%d")
    ext = "txt" if software == "yayoi" else "csv"
    return data, f"journal_{software}_{stamp}.{ext}", replaced


def _yayoi(entries, account, labels):
    """弥生インポート形式(25項目、見出し行なし)。複数行の伝票は識別フラグで1枚の振替伝票にまとめる。"""
    rows = []
    for e in entries:
        n = len(e["lines"])
        for i, line in enumerate(e["lines"]):
            flag = "2111" if n == 1 else "2110" if i == 0 else "2101" if i == n - 1 else "2100"
            d, c = line["debit"], line["credit"]
            dn, ds, _ = account(d, e["partner"])
            cn, cs, _ = account(c, e["partner"])
            rows.append([
                flag, "", "", e["date"].replace("-", "/"),
                dn, ds, "", labels[d["tax"]], _yen(d["amount"]), _tax_amount(d),
                cn, cs, "", labels[c["tax"]], _yen(c["amount"]), _tax_amount(c),
                e["summary"][:64], "", "", "3", "", "", "", "", "no",
            ])
    return None, rows


MF_HEADER = [
    "取引No", "取引日", "借方勘定科目", "借方補助科目", "借方部門", "借方取引先", "借方税区分", "借方インボイス",
    "借方金額(円)", "借方税額", "貸方勘定科目", "貸方補助科目", "貸方部門", "貸方取引先", "貸方税区分", "貸方インボイス",
    "貸方金額(円)", "貸方税額", "摘要", "仕訳メモ", "タグ", "MF仕訳タイプ", "決算整理仕訳",
    "作成日時", "作成者", "最終更新日時", "最終更新者",
]


def _mf(entries, account, labels):
    """マネーフォワード クラウド会計の仕訳帳インポート形式。同じ取引Noの行が1つの複合仕訳になる。
    税額は「0 または空欄」の指定なので空欄にし、会計ソフト側で計算させる。"""
    rows = []
    for no, e in enumerate(entries, start=1):
        for line in e["lines"]:
            d, c = line["debit"], line["credit"]
            dn, ds, _ = account(d, e["partner"])
            cn, cs, _ = account(c, e["partner"])
            dp = e["partner"] if d["account"] == "receivable" else ""
            cp = e["partner"] if c["account"] == "receivable" else ""
            rows.append([
                no, e["date"].replace("-", "/"),
                dn, ds, "", dp, labels[d["tax"]], "", _yen(d["amount"]), "",
                cn, cs, "", cp, labels[c["tax"]], "", _yen(c["amount"]), "",
                e["summary"][:200], "", "", "", "", "", "", "", "",
            ])
    return MF_HEADER, rows


FREEE_SIDE = ["勘定科目", "科目コード", "補助科目", "取引先", "取引先コード", "部門", "品目", "メモタグ",
              "セグメント1", "セグメント2", "セグメント3", "金額", "税区分", "税額"]
FREEE_HEADER = (["[表題行]", "日付", "伝票番号", "決算整理仕訳"]
                + [f"借方{c}" for c in FREEE_SIDE] + [f"貸方{c}" for c in FREEE_SIDE] + ["摘要"])


def _freee(entries, account, labels):
    """freee会計の「他社会計ソフト仕訳のインポート」(振替伝票)の形式。同じ伝票番号の行が1つの仕訳になる。"""
    rows = []
    for no, e in enumerate(entries, start=1):
        for line in e["lines"]:
            row = ["[明細行]", e["date"].replace("-", "/"), no, ""]
            for s in (line["debit"], line["credit"]):
                name, sub, code = account(s, e["partner"])
                partner = e["partner"] if s["account"] == "receivable" else ""
                row += [name, code, sub, partner or "", "", "", "", "", "", "", "",
                        _yen(s["amount"]), labels[s["tax"]], _tax_amount(s)]
            row.append(e["summary"][:1024])
            rows.append(row)
    return FREEE_HEADER, rows


BUGYO_HEADER = [
    "OBCD001", "CSJS005",
    "CSJS200", "CSJS201", "CSJS202", "CSJS203", "CSJS222", "CSJS220", "CSJS206", "CSJS208", "CSJS213",
    "CSJS300", "CSJS301", "CSJS302", "CSJS303", "CSJS322", "CSJS320", "CSJS306", "CSJS308", "CSJS313",
    "CSJS100",
]
# 税区分の種類 → (税率種別 0=標準 1=軽減, 税率)
_BUGYO_RATE = {"sales_standard": ("0", "10"), "sales_reduced": ("1", "8"), "purchase_standard": ("0", "10")}


def _bugyo(entries, account, labels):
    """勘定奉行の仕訳伝票(OBC受入形式・受入記号付き)。1行目が受入記号、伝票の1行目に「*」を付ける。
    金額は税込の本体金額とし、消費税は税込金額から自動計算させる(消費税自動計算=2)。"""
    rows = []
    for e in entries:
        for i, line in enumerate(e["lines"]):
            row = ["*" if i == 0 else "", e["date"].replace("-", "/")]
            for s in (line["debit"], line["credit"]):
                _, _, code = account(s, e["partner"])
                rate_type, rate = _BUGYO_RATE.get(s["tax"], ("", ""))
                row += ["", code, "", labels[s["tax"]], rate_type, rate, "2", "", _yen(s["amount"])]
            row.append(e["summary"][:40])
            rows.append(row)
    return BUGYO_HEADER, rows


_ROWS = {"yayoi": _yayoi, "mf": _mf, "freee": _freee, "kanjo_bugyo": _bugyo}
