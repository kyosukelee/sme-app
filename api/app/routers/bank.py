"""入金消込。

1. 銀行の入出金明細(CSV)を、取込形式(列の対応)に沿って取り込む。入金の行だけを明細として保存する
2. 明細ごとに、振込名義と金額から、消し込む請求書の候補を出す
3. 人が候補を確かめて確定すると、入金(receipts)を作って請求書に割り当てる。
   振込手数料を差し引かれた差額は、会社設定の上限以内なら「振込手数料」として消し込む

明細を確定したあとに「入金」の画面で入金を取り消すと、明細は未消込に戻る。
"""

import base64
import binascii
import hashlib
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from psycopg import Connection, errors
from pydantic import BaseModel, Field, model_validator

from app.auth import TenantConn
from app.domain.bank import decode, normalize_payer, parse_amount, parse_date, payer_similarity, read_rows
from app.routers.receipts import _get_receipt, delete_receipt, get_any_customer, open_invoice_rows, record_receipt

router = APIRouter(prefix="/api/bank", tags=["bank"])

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB(1年分の明細でも十分な大きさ)
PREVIEW_ROWS = 12


# ---------------------------------------------------------------------
# 取込形式(列の対応)
# ---------------------------------------------------------------------
class BankFormatIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    encoding: Literal["auto", "utf-8", "shift_jis"] = "auto"
    skip_rows: int = Field(default=1, ge=0, le=20)
    date_col: int = Field(ge=0, le=99)
    amount_col: int = Field(ge=0, le=99)
    payer_col: int = Field(ge=0, le=99)
    memo_col: int | None = Field(default=None, ge=0, le=99)

    @model_validator(mode="after")
    def columns_differ(self) -> "BankFormatIn":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("取込形式の名前を入力してください")
        if len({self.date_col, self.amount_col, self.payer_col}) < 3:
            raise ValueError("取引日・入金額・振込名義には、それぞれ別の列を選んでください")
        return self


class BankFormatOut(BankFormatIn):
    id: UUID


FORMAT_COLUMNS = "id, name, encoding, skip_rows, date_col, amount_col, payer_col, memo_col"


def _write_format(conn: Connection, sql: str, params: dict):
    try:
        with conn.transaction():
            return conn.execute(sql, params).fetchone()
    except errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, f"取込形式「{params['name']}」はすでにあります") from None


@router.get("/formats", response_model=list[BankFormatOut])
def list_formats(conn: TenantConn):
    return conn.execute(f"SELECT {FORMAT_COLUMNS} FROM app.bank_formats ORDER BY name").fetchall()


@router.post("/formats", response_model=BankFormatOut, status_code=status.HTTP_201_CREATED)
def create_format(body: BankFormatIn, conn: TenantConn):
    return _write_format(
        conn,
        f"""
        INSERT INTO app.bank_formats (tenant_id, name, encoding, skip_rows, date_col, amount_col, payer_col, memo_col)
        VALUES (app.current_tenant_id(), %(name)s, %(encoding)s, %(skip_rows)s, %(date_col)s, %(amount_col)s,
                %(payer_col)s, %(memo_col)s)
        RETURNING {FORMAT_COLUMNS}
        """,
        body.model_dump(),
    )


@router.put("/formats/{format_id}", response_model=BankFormatOut)
def update_format(format_id: UUID, body: BankFormatIn, conn: TenantConn):
    row = _write_format(
        conn,
        f"""
        UPDATE app.bank_formats
        SET name = %(name)s, encoding = %(encoding)s, skip_rows = %(skip_rows)s, date_col = %(date_col)s,
            amount_col = %(amount_col)s, payer_col = %(payer_col)s, memo_col = %(memo_col)s
        WHERE id = %(id)s
        RETURNING {FORMAT_COLUMNS}
        """,
        {**body.model_dump(), "id": format_id},
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "取込形式が見つかりません")
    return row


@router.delete("/formats/{format_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_format(format_id: UUID, conn: TenantConn):
    """取込形式を削除する。取り込み済みの明細はそのまま残る。"""
    if conn.execute("DELETE FROM app.bank_formats WHERE id = %s RETURNING id", (format_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "取込形式が見つかりません")


# ---------------------------------------------------------------------
# CSV の確認(列の対応を決めるためのプレビュー)と取り込み
# ---------------------------------------------------------------------
class FileIn(BaseModel):
    filename: str | None = Field(default=None, max_length=200)
    content_base64: str = Field(min_length=1)


class PreviewIn(FileIn):
    encoding: Literal["auto", "utf-8", "shift_jis"] = "auto"


class ColumnGuess(BaseModel):
    skip_rows: int
    date_col: int | None
    amount_col: int | None
    payer_col: int | None
    memo_col: int | None


class PreviewOut(BaseModel):
    encoding: str
    rows: list[list[str]]
    total_rows: int
    guess: ColumnGuess


def _file_bytes(body: FileIn) -> bytes:
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ファイルを読み込めませんでした") from None
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ファイルが大きすぎます(2MBまで)。期間を分けて取り込んでください")
    return data


def _rows(data: bytes, encoding: str) -> tuple[list[list[str]], str]:
    try:
        text, used = decode(data, encoding)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    return read_rows(text), used


# 見出しの言葉から列を推測する(銀行ごとに書き方が違うので、よく使われる言葉を並べる)
_HEADER_WORDS = {
    "date_col": ("取引日", "日付", "年月日", "お取引日", "取扱日", "勘定日", "入出金日"),
    "amount_col": ("入金", "お預入", "預入", "お預り", "預かり", "受入"),
    "payer_col": ("振込依頼人", "依頼人", "摘要", "お取引内容", "取引内容", "内容", "名義", "お取引先"),
    "memo_col": ("メモ", "備考", "取引区分", "区分"),
}


def guess_columns(rows: list[list[str]]) -> dict:
    """先頭の行から、見出しの行と各列の位置を推測する。分からない列は None。"""
    for index, row in enumerate(rows[:10]):
        found: dict[str, int | None] = {}
        for key, words in _HEADER_WORDS.items():
            found[key] = next(
                (i for i, cell in enumerate(row) if i not in found.values() and any(w in cell for w in words)), None
            )
        if found["date_col"] is not None and found["amount_col"] is not None:
            return {"skip_rows": index + 1, **found}
    return {"skip_rows": 0, "date_col": None, "amount_col": None, "payer_col": None, "memo_col": None}


@router.post("/preview", response_model=PreviewOut)
def preview(body: PreviewIn, conn: TenantConn):
    rows, used = _rows(_file_bytes(body), body.encoding)
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ファイルに行がありません")
    return PreviewOut(encoding=used, rows=rows[:PREVIEW_ROWS], total_rows=len(rows), guess=guess_columns(rows))


class ImportIn(FileIn):
    format_id: UUID


class ImportProblem(BaseModel):
    row: int  # ファイルの何行目か(1から)
    message: str


class ImportOut(BaseModel):
    id: UUID
    imported: int  # 新しく取り込んだ入金の明細
    duplicates: int  # 取り込み済みのため飛ばした明細
    skipped: int  # 出金や金額が空の行
    problems: list[ImportProblem]  # 読めなかった行(先頭20件まで)


def _cell(row: list[str], col: int | None) -> str:
    return row[col] if col is not None and col < len(row) else ""


@router.post("/imports", response_model=ImportOut, status_code=status.HTTP_201_CREATED)
def import_statements(body: ImportIn, conn: TenantConn):
    fmt = conn.execute(f"SELECT {FORMAT_COLUMNS} FROM app.bank_formats WHERE id = %s", (body.format_id,)).fetchone()
    if fmt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "取込形式が見つかりません")
    rows, _ = _rows(_file_bytes(body), fmt["encoding"])

    entries, problems, skipped = [], [], 0
    seen: Counter = Counter()
    for number, row in enumerate(rows[fmt["skip_rows"]:], start=fmt["skip_rows"] + 1):
        amount = parse_amount(_cell(row, fmt["amount_col"]))
        if amount is None or amount <= 0:
            skipped += 1  # 出金の行(入金額が空)や、入金額が 0 の行
            continue
        txn_date = parse_date(_cell(row, fmt["date_col"]))
        payer = _cell(row, fmt["payer_col"])
        if txn_date is None:
            problems.append({"row": number, "message": f"取引日「{_cell(row, fmt['date_col'])}」を日付として読めません"})
            continue
        if amount != amount.to_integral_value():
            problems.append({"row": number, "message": f"入金額「{_cell(row, fmt['amount_col'])}」に円未満があります"})
            continue
        if not payer:
            problems.append({"row": number, "message": "振込名義が空です"})
            continue
        memo = _cell(row, fmt["memo_col"]) or None
        key = normalize_payer(payer)
        # 同じ日・同じ金額・同じ名義の振込が2件あっても区別できるよう、ファイル内での出現回数も含める
        base = f"{txn_date}|{amount}|{key}|{memo or ''}"
        seen[base] += 1
        fingerprint = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
        entries.append((txn_date, amount, payer, key, memo, fingerprint))

    if not entries and problems:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{problems[0]['row']}行目: {problems[0]['message']}。取込形式の列の対応を確認してください",
        )

    import_id = conn.execute(
        """
        INSERT INTO app.bank_imports (tenant_id, format_id, filename, created_by)
        VALUES (app.current_tenant_id(), %s, %s, app.current_user_id()) RETURNING id
        """,
        (body.format_id, body.filename),
    ).fetchone()["id"]
    imported = 0
    for txn_date, amount, payer, key, memo, fingerprint in entries:
        inserted = conn.execute(
            """
            INSERT INTO app.bank_statements
              (tenant_id, import_id, txn_date, amount, payer_name, payer_key, memo, fingerprint)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, fingerprint) DO NOTHING
            RETURNING id
            """,
            (import_id, txn_date, amount, payer, key, memo, fingerprint),
        ).fetchone()
        imported += inserted is not None
    duplicates = len(entries) - imported
    conn.execute(
        "UPDATE app.bank_imports SET imported_count = %s, duplicate_count = %s WHERE id = %s",
        (imported, duplicates, import_id),
    )
    return ImportOut(id=import_id, imported=imported, duplicates=duplicates, skipped=skipped, problems=problems[:20])


class ImportSummary(BaseModel):
    id: UUID
    filename: str | None
    format_name: str | None
    imported_count: int
    duplicate_count: int
    matched_count: int
    created_at: datetime


@router.get("/imports", response_model=list[ImportSummary])
def list_imports(conn: TenantConn):
    return conn.execute(
        """
        SELECT b.id, b.filename, f.name AS format_name, b.imported_count, b.duplicate_count, b.created_at,
               (SELECT count(*) FROM app.bank_statements s WHERE s.import_id = b.id AND s.receipt_id IS NOT NULL)
                 AS matched_count
        FROM app.bank_imports b LEFT JOIN app.bank_formats f ON f.id = b.format_id
        ORDER BY b.created_at DESC LIMIT 20
        """
    ).fetchall()


@router.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(import_id: UUID, conn: TenantConn):
    """間違えて取り込んだファイルを取り消す。消込を確定した明細があるときは取り消せない。"""
    row = conn.execute("SELECT id FROM app.bank_imports WHERE id = %s FOR UPDATE", (import_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "取り込みが見つかりません")
    matched = conn.execute(
        "SELECT count(*) AS n FROM app.bank_statements WHERE import_id = %s AND receipt_id IS NOT NULL", (import_id,)
    ).fetchone()["n"]
    if matched:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"消込を確定した明細が{matched}件あります。先に確定を取り消してください"
        )
    conn.execute("DELETE FROM app.bank_statements WHERE import_id = %s", (import_id,))
    conn.execute("DELETE FROM app.bank_imports WHERE id = %s", (import_id,))


# ---------------------------------------------------------------------
# 明細と候補
# ---------------------------------------------------------------------
class CandidateInvoice(BaseModel):
    invoice_id: UUID
    invoice_number: str
    version: int
    title: str | None
    issue_date: date
    due_date: date | None
    balance: Decimal


class Candidate(BaseModel):
    customer_id: UUID
    customer_name: str
    basis: Literal["name_amount", "amount", "name"]  # 名義と金額が合う / 金額だけ合う / 名義だけ合う
    match: Literal["exact", "fee", "partial"]  # ちょうど / 振込手数料の差額あり / 一部入金
    fee_amount: Decimal
    invoices: list[CandidateInvoice]


class ReceiptBrief(BaseModel):
    id: UUID
    customer_id: UUID
    customer_name: str
    fee_amount: Decimal
    invoice_numbers: list[str]


class StatementOut(BaseModel):
    id: UUID
    txn_date: date
    amount: Decimal
    payer_name: str
    memo: str | None
    status: Literal["open", "matched", "ignored"]
    ignore_reason: str | None
    receipt: ReceiptBrief | None
    candidates: list[Candidate]


class StatementsOut(BaseModel):
    statements: list[StatementOut]
    counts: dict[str, int]
    fee_limit: Decimal


def _fee_limit(conn: Connection) -> Decimal:
    return conn.execute("SELECT bank_fee_limit FROM app.tenant_settings").fetchone()["bank_fee_limit"]


def _all_open_invoices(conn: Connection) -> dict[UUID, list[dict]]:
    """未入金の請求書を、取引先ごとに古い順で返す。"""
    rows = conn.execute(
        """
        SELECT i.customer_id, i.id AS invoice_id, i.invoice_number, i.version, i.title, i.issue_date, i.due_date,
               i.total_amount - i.withholding_amount
                 - coalesce((SELECT sum(p.amount) FROM app.payments p WHERE p.invoice_id = i.id), 0) AS balance
        FROM app.invoices i
        WHERE i.status = 'issued' AND i.superseded_at IS NULL
        ORDER BY i.issue_date, i.invoice_number
        """
    ).fetchall()
    by_customer: dict[UUID, list[dict]] = defaultdict(list)
    for r in rows:
        if r["balance"] > 0:
            by_customer[r["customer_id"]].append(r)
    return by_customer


def _amount_matches(amount: Decimal, invoices: list[dict], fee_limit: Decimal) -> list[tuple[str, Decimal, list[dict]]]:
    """入金額に合う請求書の組み合わせ。1枚でちょうど/差額が手数料以内、古い順の合計でちょうど/手数料以内。"""
    found = []
    for inv in invoices:
        diff = inv["balance"] - amount
        if diff == 0:
            found.append(("exact", Decimal(0), [inv]))
        elif 0 < diff <= fee_limit:
            found.append(("fee", diff, [inv]))
    total = Decimal(0)
    for k, inv in enumerate(invoices):
        total += inv["balance"]
        if k == 0:
            continue  # 1枚の場合は上で調べた
        diff = total - amount
        if diff == 0:
            found.append(("exact", Decimal(0), invoices[: k + 1]))
        elif 0 < diff <= fee_limit:
            found.append(("fee", diff, invoices[: k + 1]))
        if diff >= 0:
            break
    # ちょうど合うものを先に
    return sorted(found, key=lambda f: (f[0] != "exact", len(f[2])))


def _partial(amount: Decimal, invoices: list[dict]) -> list[dict] | None:
    """一部入金として古い順に割り当てるときに使う請求書。入金額が残額の合計を超えるなら None。"""
    if not invoices or amount > sum((inv["balance"] for inv in invoices), Decimal(0)):
        return None
    picked, rest = [], amount
    for inv in invoices:
        picked.append(inv)
        rest -= inv["balance"]
        if rest <= 0:
            break
    return picked


def candidates_for(
    statement: dict, customers: list[dict], open_by_customer: dict[UUID, list[dict]], fee_limit: Decimal
) -> list[dict]:
    """明細1件の候補(よさそうな順に最大3件)。"""
    amount = statement["amount"]
    scored: list[tuple[int, dict]] = []

    def add(score: int, customer: dict, basis: str, match: str, fee: Decimal, invoices: list[dict]) -> None:
        scored.append((score, {
            "customer_id": customer["id"], "customer_name": customer["name"], "basis": basis, "match": match,
            "fee_amount": fee, "invoices": invoices,
        }))

    name_matched = False
    for c in customers:
        similarity = max((payer_similarity(statement["payer_key"], k) for k in c["payer_keys"]), default=0)
        invoices = open_by_customer.get(c["id"], [])
        if similarity:
            name_matched = True
            matches = _amount_matches(amount, invoices, fee_limit)
            for match, fee, picked in matches[:2]:
                add(100 + similarity * 10 - (5 if match == "fee" else 0) - len(picked), c, "name_amount", match, fee, picked)
            if not matches and invoices:
                picked = _partial(amount, invoices)
                if picked is not None:
                    add(50 + similarity * 10, c, "name", "partial", Decimal(0), picked)

    if not name_matched:
        # 名義が登録されていない取引先からの入金。金額だけで探す(確定するときに名義を覚えさせられる)
        for c in customers:
            for match, fee, picked in _amount_matches(amount, open_by_customer.get(c["id"], []), fee_limit)[:1]:
                add(40 - (5 if match == "fee" else 0) - len(picked), c, "amount", match, fee, picked)

    scored.sort(key=lambda s: -s[0])
    return [candidate for _, candidate in scored[:3]]


def _customers_with_keys(conn: Connection) -> list[dict]:
    rows = conn.execute("SELECT id, name, payer_names FROM app.customers ORDER BY is_active DESC, name").fetchall()
    for r in rows:
        r["payer_keys"] = {normalize_payer(n) for n in r["payer_names"]} - {""}
    return rows


def _receipt_briefs(conn: Connection, receipt_ids: list[UUID]) -> dict[UUID, dict]:
    if not receipt_ids:
        return {}
    rows = conn.execute(
        """
        SELECT r.id, r.customer_id, c.name AS customer_name, r.fee_amount,
               coalesce(array_agg(DISTINCT i.invoice_number || CASE WHEN i.version > 1 THEN '(第' || i.version || '版)' ELSE '' END)
                        FILTER (WHERE i.id IS NOT NULL), '{}') AS invoice_numbers
        FROM app.receipts r
        JOIN app.customers c ON c.id = r.customer_id
        LEFT JOIN app.payments p ON p.receipt_id = r.id
        LEFT JOIN app.invoices i ON i.id = p.invoice_id
        WHERE r.id = ANY(%s::uuid[])
        GROUP BY r.id, c.name
        """,
        (receipt_ids,),
    ).fetchall()
    return {r["id"]: r for r in rows}


@router.get("/statements", response_model=StatementsOut)
def list_statements(
    conn: TenantConn, status_filter: Literal["open", "matched", "ignored"] = Query("open", alias="status")
):
    where = {
        "open": "receipt_id IS NULL AND NOT ignored ORDER BY txn_date, created_at",
        "matched": "receipt_id IS NOT NULL ORDER BY txn_date DESC, created_at DESC LIMIT 200",
        "ignored": "ignored ORDER BY txn_date DESC, created_at DESC LIMIT 200",
    }[status_filter]
    rows = conn.execute(
        f"""
        SELECT id, txn_date, amount, payer_name, payer_key, memo, receipt_id, ignored, ignore_reason
        FROM app.bank_statements WHERE {where}
        """
    ).fetchall()
    counts = conn.execute(
        """
        SELECT count(*) FILTER (WHERE receipt_id IS NULL AND NOT ignored) AS open,
               count(*) FILTER (WHERE receipt_id IS NOT NULL) AS matched,
               count(*) FILTER (WHERE ignored) AS ignored
        FROM app.bank_statements
        """
    ).fetchone()
    fee_limit = _fee_limit(conn)

    customers, open_by_customer = [], {}
    if status_filter == "open" and rows:
        customers = _customers_with_keys(conn)
        open_by_customer = _all_open_invoices(conn)
    briefs = _receipt_briefs(conn, [r["receipt_id"] for r in rows if r["receipt_id"]])

    out = []
    for r in rows:
        state = "matched" if r["receipt_id"] else "ignored" if r["ignored"] else "open"
        out.append({
            **r,
            "status": state,
            "receipt": briefs.get(r["receipt_id"]),
            "candidates": candidates_for(r, customers, open_by_customer, fee_limit) if state == "open" else [],
        })
    return StatementsOut(statements=out, counts=counts, fee_limit=fee_limit)


# ---------------------------------------------------------------------
# 確定・取消・対象外
# ---------------------------------------------------------------------
class MatchIn(BaseModel):
    customer_id: UUID
    invoice_ids: list[UUID] = Field(min_length=1, max_length=50)
    fee_amount: Decimal = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=0)
    remember_payer: bool = True  # この振込名義を取引先に登録して、次から名義で見つけられるようにする


class IgnoreIn(BaseModel):
    reason: str | None = Field(default=None, max_length=100)


def _lock_statement(conn: Connection, statement_id: UUID) -> dict:
    row = conn.execute(
        """
        SELECT id, txn_date, amount, payer_name, payer_key, receipt_id, ignored
        FROM app.bank_statements WHERE id = %s FOR UPDATE
        """,
        (statement_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "明細が見つかりません")
    return row


def _statement_out(conn: Connection, statement_id: UUID) -> dict:
    r = conn.execute(
        """
        SELECT id, txn_date, amount, payer_name, payer_key, memo, receipt_id, ignored, ignore_reason
        FROM app.bank_statements WHERE id = %s
        """,
        (statement_id,),
    ).fetchone()
    state = "matched" if r["receipt_id"] else "ignored" if r["ignored"] else "open"
    briefs = _receipt_briefs(conn, [r["receipt_id"]] if r["receipt_id"] else [])
    return {**r, "status": state, "receipt": briefs.get(r["receipt_id"]), "candidates": []}


@router.post("/statements/{statement_id}/match", response_model=StatementOut)
def match_statement(statement_id: UUID, body: MatchIn, conn: TenantConn):
    """明細を請求書に消し込む(入金を登録する)。"""
    statement = _lock_statement(conn, statement_id)
    if statement["receipt_id"] or statement["ignored"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "この明細はすでに処理されています。画面を読み込み直してください")
    get_any_customer(conn, body.customer_id)

    wanted = set(body.invoice_ids)
    invoices = [r for r in open_invoice_rows(conn, body.customer_id, lock=True) if r["invoice_id"] in wanted]
    if len(invoices) != len(wanted):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "選んだ請求書のなかに、入金済みになったか、訂正・取消されたものがあります。画面を読み込み直してください",
        )
    amount, fee = statement["amount"], body.fee_amount
    total = sum((r["balance"] for r in invoices), Decimal(0))
    limit = _fee_limit(conn)
    if fee > limit:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"振込手数料として消し込めるのは{limit:,}円までです(上限は自社情報の画面で変えられます)",
        )
    if amount + fee > total:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"入金額({amount:,}円)が、選んだ請求書の残額の合計({total:,}円)を超えています。請求書を追加して選んでください",
        )
    if fee and amount + fee != total:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"振込手数料は、残額の合計と入金額の差額({total - amount:,}円)にしてください",
        )

    receipt_id = record_receipt(
        conn, customer_id=body.customer_id, received_on=statement["txn_date"], amount=amount, fee=fee,
        memo=f"入金消込({statement['payer_name']})", payment_memo="入金消込", open_invoices=invoices,
    )
    conn.execute("UPDATE app.bank_statements SET receipt_id = %s WHERE id = %s", (receipt_id, statement_id))

    if body.remember_payer and statement["payer_key"]:
        names = conn.execute(
            "SELECT payer_names FROM app.customers WHERE id = %s", (body.customer_id,)
        ).fetchone()["payer_names"]
        if statement["payer_key"] not in {normalize_payer(n) for n in names} and len(names) < 10:
            conn.execute(
                "UPDATE app.customers SET payer_names = array_append(payer_names, %s) WHERE id = %s",
                (statement["payer_name"], body.customer_id),
            )
    return _statement_out(conn, statement_id)


@router.delete("/statements/{statement_id}/match", response_model=StatementOut)
def unmatch_statement(statement_id: UUID, conn: TenantConn):
    """確定を取り消す。登録した入金も取り消し、請求書は未入金に戻る。"""
    statement = _lock_statement(conn, statement_id)
    if not statement["receipt_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "この明細は消込を確定していません")
    _get_receipt(conn, statement["receipt_id"])
    delete_receipt(statement["receipt_id"], conn)  # 明細の receipt_id は外部キーの設定で空に戻る
    return _statement_out(conn, statement_id)


@router.post("/statements/{statement_id}/ignore", response_model=StatementOut)
def ignore_statement(statement_id: UUID, body: IgnoreIn, conn: TenantConn):
    """請求書と関係のない入金(利息、返金、売上以外の入金など)を、消込の対象外にする。"""
    statement = _lock_statement(conn, statement_id)
    if statement["receipt_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "消込を確定した明細は対象外にできません。先に確定を取り消してください")
    conn.execute(
        "UPDATE app.bank_statements SET ignored = true, ignore_reason = %s WHERE id = %s",
        ((body.reason or "").strip() or None, statement_id),
    )
    return _statement_out(conn, statement_id)


@router.delete("/statements/{statement_id}/ignore", response_model=StatementOut)
def unignore_statement(statement_id: UUID, conn: TenantConn):
    _lock_statement(conn, statement_id)
    conn.execute("UPDATE app.bank_statements SET ignored = false, ignore_reason = NULL WHERE id = %s", (statement_id,))
    return _statement_out(conn, statement_id)

