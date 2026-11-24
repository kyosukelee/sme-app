"""会計ソフト向けの仕訳CSV。

出力するもの
  ・売上: 発行した請求書(最新版)。税率ごとに「売掛金 / 売上高」。源泉徴収があれば「仮払税金(事業主貸) / 売掛金」
  ・入金: 入金(まとめ入金・入金消込)は「普通預金 / 売掛金」、振込手数料は「支払手数料 / 売掛金」。
          請求書の画面で直接登録した入金も同じ
  ・逆仕訳: 出力したあとに訂正・取消した請求書、取り消した入金は、借方と貸方を入れ替えた仕訳を次の出力で出す

一度出力したものは記録し、次の出力には含めない(二重計上を防ぐ)。間違えて出力したときは、
最後の出力に限り取り消して出し直せる。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from app import activity
from app.auth import AdminConn, TenantConn
from app.clock import today
from app.domain.journal import (
    ACCOUNT_KEYS, DEFAULT_TAX_LABELS, SALES_TAX_KEY, SOFTWARE_NAMES, TAX_KEYS, SettingsProblem, default_accounts,
    render, reverse_lines, side,
)

router = APIRouter(prefix="/api/journal", tags=["journal"])

Software = Literal["kanjo_bugyo", "yayoi", "freee", "mf"]


# ---------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------
class AccountSetting(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    code: str = Field(default="", max_length=20)  # 勘定奉行の勘定科目コード
    sub: str = Field(default="", max_length=30)  # 補助科目(入金口座の銀行名など)


class JournalSettings(BaseModel):
    software: Software = "yayoi"
    sales_date_basis: Literal["transaction", "issue"] = "transaction"
    customer_as_sub: bool = True
    accounts: dict[str, AccountSetting]
    tax_labels: dict[str, dict[str, str]]  # 会計ソフトごとの税区分の名前(勘定奉行はコード)

    @model_validator(mode="after")
    def complete(self) -> "JournalSettings":
        if set(self.accounts) != set(ACCOUNT_KEYS):
            raise ValueError("勘定科目の設定が足りません")
        for software, labels in self.tax_labels.items():
            if software not in SOFTWARE_NAMES or set(labels) != set(TAX_KEYS):
                raise ValueError("税区分の設定が足りません")
            for value in labels.values():
                if len(value) > 32:
                    raise ValueError("税区分は32文字以内で入力してください")
        return self


class JournalSettingsOut(JournalSettings):
    can_edit: bool
    defaults: dict[str, dict[str, str]]  # 会計ソフトごとの税区分の初期値(元に戻すため)


def _sole_proprietor(conn: Connection) -> bool:
    row = conn.execute("SELECT business_type FROM app.company_profiles").fetchone()
    return bool(row and row["business_type"] == "sole_proprietor")


def load_settings(conn: Connection) -> dict:
    """保存した設定に初期値を補って返す。"""
    row = conn.execute(
        "SELECT software, sales_date_basis, customer_as_sub, accounts, tax_labels FROM app.journal_settings"
    ).fetchone()
    accounts = default_accounts(_sole_proprietor(conn))
    stored_accounts = row["accounts"] if row else {}
    for key in ACCOUNT_KEYS:
        accounts[key] = {**accounts[key], **stored_accounts.get(key, {})}
    stored_labels = row["tax_labels"] if row else {}
    labels = {sw: {**DEFAULT_TAX_LABELS[sw], **stored_labels.get(sw, {})} for sw in SOFTWARE_NAMES}
    software = row["software"] if row else "yayoi"
    return {
        "software": software,
        "sales_date_basis": row["sales_date_basis"] if row else "transaction",
        "customer_as_sub": row["customer_as_sub"] if row else True,
        "accounts": accounts,
        "tax_labels": labels,
    }


def _is_admin(conn: Connection) -> bool:
    role = conn.execute(
        "SELECT role FROM app.memberships WHERE tenant_id = app.current_tenant_id() AND user_id = app.current_user_id()"
    ).fetchone()
    return role is not None and role["role"] == "admin"


@router.get("/settings", response_model=JournalSettingsOut)
def get_settings(conn: TenantConn):
    return {**load_settings(conn), "can_edit": _is_admin(conn), "defaults": DEFAULT_TAX_LABELS}


@router.put("/settings", response_model=JournalSettingsOut)
def update_settings(body: JournalSettings, conn: AdminConn):
    accounts = {k: v.model_dump() for k, v in body.accounts.items()}
    labels = {sw: {k: v.strip() for k, v in body.tax_labels.get(sw, {}).items()} for sw in body.tax_labels}
    conn.execute(
        """
        INSERT INTO app.journal_settings (tenant_id, software, sales_date_basis, customer_as_sub, accounts, tax_labels)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET software = EXCLUDED.software, sales_date_basis = EXCLUDED.sales_date_basis,
            customer_as_sub = EXCLUDED.customer_as_sub, accounts = EXCLUDED.accounts, tax_labels = EXCLUDED.tax_labels
        """,
        (body.software, body.sales_date_basis, body.customer_as_sub, Jsonb(accounts), Jsonb(labels)),
    )
    return get_settings(conn)


# ---------------------------------------------------------------------
# 仕訳の組み立て
# ---------------------------------------------------------------------
def _invoice_summary(inv: dict) -> str:
    version = f"(第{inv['version']}版)" if inv["version"] > 1 else ""
    title = f" {inv['title']}" if inv["title"] else ""
    return f"{inv['customer_name']} 請求書{inv['invoice_number']}{version}{title}"


def _pending_invoices(conn: Connection, basis: str) -> list[dict]:
    invoices = conn.execute(
        """
        SELECT i.id, i.invoice_number, i.version, i.title, i.customer_name, i.issue_date, i.closing_date,
               i.withholding_amount, i.issuer_tax_exempt,
               (SELECT max(l.transaction_date) FROM app.invoice_lines l WHERE l.invoice_id = i.id) AS last_transaction
        FROM app.invoices i
        WHERE i.status IN ('issued', 'paid') AND i.superseded_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM app.journal_export_items x
                          WHERE x.source_type = 'invoice' AND x.source_id = i.id AND x.kind = 'entry')
        """
    ).fetchall()
    if not invoices:
        return []
    summaries: dict[UUID, list[dict]] = {}
    for s in conn.execute(
        """
        SELECT invoice_id, tax_category, taxable_amount, tax_amount FROM app.invoice_tax_summaries
        WHERE invoice_id = ANY(%s::uuid[]) ORDER BY invoice_id, tax_rate DESC, tax_category
        """,
        ([i["id"] for i in invoices],),
    ).fetchall():
        summaries.setdefault(s["invoice_id"], []).append(s)

    entries = []
    for inv in invoices:
        if basis == "issue":
            entry_date = inv["issue_date"]
        else:
            # 取引日基準: 締め請求は締め日、それ以外は明細の最も遅い取引日(納品日)
            entry_date = inv["closing_date"] or inv["last_transaction"] or inv["issue_date"]
        lines = []
        for s in summaries.get(inv["id"], []):
            gross = s["taxable_amount"] + s["tax_amount"]
            if gross == 0:
                continue
            # 免税事業者として発行した請求書の売上は、消費税の対象外として出す
            tax = "out_of_scope" if inv["issuer_tax_exempt"] else SALES_TAX_KEY[s["tax_category"]]
            lines.append({"debit": side("receivable", gross), "credit": side("sales", gross, tax, s["tax_amount"])})
        if inv["withholding_amount"]:
            lines.append({"debit": side("withholding", inv["withholding_amount"]),
                          "credit": side("receivable", inv["withholding_amount"])})
        if not lines:
            continue
        entries.append({
            "source_type": "invoice", "source_id": inv["id"], "kind": "entry", "date": entry_date,
            "summary": _invoice_summary(inv), "partner": inv["customer_name"], "lines": lines,
        })
    return entries


def _pending_receipts(conn: Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT r.id, r.received_on, r.amount, r.fee_amount, r.memo, c.name AS customer_name
        FROM app.receipts r JOIN app.customers c ON c.id = r.customer_id
        WHERE NOT EXISTS (SELECT 1 FROM app.journal_export_items x
                          WHERE x.source_type = 'receipt' AND x.source_id = r.id AND x.kind = 'entry')
        """
    ).fetchall()
    entries = []
    for r in rows:
        lines = [{"debit": side("bank", r["amount"]), "credit": side("receivable", r["amount"])}]
        if r["fee_amount"]:
            lines.append({"debit": side("fee", r["fee_amount"], "purchase_standard"),
                          "credit": side("receivable", r["fee_amount"])})
        memo = f" {r['memo']}" if r["memo"] else ""
        entries.append({
            "source_type": "receipt", "source_id": r["id"], "kind": "entry", "date": r["received_on"],
            "summary": f"{r['customer_name']} 入金{memo}", "partner": r["customer_name"], "lines": lines,
        })
    return entries


def _pending_payments(conn: Connection) -> list[dict]:
    """請求書の画面で直接登録した入金(まとめ入金・入金消込を通さないもの)。マイナスは返金。"""
    rows = conn.execute(
        """
        SELECT p.id, p.paid_on, p.amount, p.memo, i.invoice_number, i.customer_name
        FROM app.payments p JOIN app.invoices i ON i.id = p.invoice_id
        WHERE p.receipt_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM app.journal_export_items x
                          WHERE x.source_type = 'payment' AND x.source_id = p.id AND x.kind = 'entry')
        """
    ).fetchall()
    entries = []
    for p in rows:
        amount = abs(p["amount"])
        lines = [{"debit": side("bank", amount), "credit": side("receivable", amount)}]
        refund = p["amount"] < 0
        memo = f" {p['memo']}" if p["memo"] else ""
        entries.append({
            "source_type": "payment", "source_id": p["id"], "kind": "entry", "date": p["paid_on"],
            "summary": f"{p['customer_name']} {'返金' if refund else '入金'} 請求書{p['invoice_number']}{memo}",
            "partner": p["customer_name"], "lines": reverse_lines(lines) if refund else lines,
        })
    return entries


def _pending_reversals(conn: Connection) -> list[dict]:
    """出力済みの仕訳のうち、元の請求書が訂正・取消された、または入金が取り消されたもの。"""
    rows = conn.execute(
        """
        SELECT x.source_type, x.source_id, x.summary, x.partner, x.lines,
               CASE x.source_type
                 WHEN 'invoice' THEN (SELECT (coalesce(i.voided_at, i.superseded_at) AT TIME ZONE 'Asia/Tokyo')::date
                                      FROM app.invoices i WHERE i.id = x.source_id)
               END AS changed_on,
               CASE x.source_type
                 WHEN 'invoice' THEN (SELECT CASE WHEN i.status = 'void' THEN '取消' ELSE '訂正' END
                                      FROM app.invoices i WHERE i.id = x.source_id)
                 ELSE '入金の取消'
               END AS reason
        FROM app.journal_export_items x
        WHERE x.kind = 'entry'
          AND NOT EXISTS (SELECT 1 FROM app.journal_export_items r
                          WHERE r.source_type = x.source_type AND r.source_id = x.source_id AND r.kind = 'reversal')
          AND CASE x.source_type
                WHEN 'invoice' THEN EXISTS (SELECT 1 FROM app.invoices i WHERE i.id = x.source_id
                                            AND (i.superseded_at IS NOT NULL OR i.status = 'void'))
                WHEN 'receipt' THEN NOT EXISTS (SELECT 1 FROM app.receipts r WHERE r.id = x.source_id)
                ELSE NOT EXISTS (SELECT 1 FROM app.payments p WHERE p.id = x.source_id)
              END
        """
    ).fetchall()
    return [{
        "source_type": r["source_type"], "source_id": r["source_id"], "kind": "reversal",
        # 入金の取消は日時を記録していないので、出力する日を逆仕訳の日付にする
        "date": r["changed_on"] or today(),
        "summary": f"【{r['reason']}】{r['summary']}", "partner": r["partner"], "lines": reverse_lines(r["lines"]),
    } for r in rows]


def pending_entries(conn: Connection, date_from: date, date_to: date) -> tuple[list[dict], int]:
    """期間内の未出力の仕訳(と、期間の終わりまでに発生した逆仕訳)。期間より前の未出力の件数も返す。"""
    settings = load_settings(conn)
    candidates = (_pending_invoices(conn, settings["sales_date_basis"]) + _pending_receipts(conn)
                  + _pending_payments(conn))
    entries = [e for e in candidates if date_from <= e["date"] <= date_to]
    before = sum(1 for e in candidates if e["date"] < date_from)
    entries += [r for r in _pending_reversals(conn) if r["date"] <= date_to]
    order = {"invoice": 0, "payment": 1, "receipt": 1}
    entries.sort(key=lambda e: (e["date"], e["kind"] == "reversal", order[e["source_type"]], e["summary"]))
    return entries, before


# ---------------------------------------------------------------------
# プレビューと出力
# ---------------------------------------------------------------------
class PreviewSide(BaseModel):
    account: str
    sub: str
    tax: str
    amount: Decimal
    tax_amount: Decimal


class PreviewEntry(BaseModel):
    kind: Literal["entry", "reversal"]
    source_type: str
    date: date
    summary: str
    lines: list[dict[str, PreviewSide]]


class PreviewOut(BaseModel):
    software: Software
    software_name: str
    entries: list[PreviewEntry]
    before_count: int  # 期間より前の日付で、まだ出力していない仕訳
    problem: str | None  # 設定が足りず出力できない理由


def _resolve(entries: list[dict], settings: dict) -> list[dict]:
    accounts, labels = settings["accounts"], settings["tax_labels"][settings["software"]]

    def one(s: dict, partner: str | None) -> dict:
        a = accounts[s["account"]]
        sub = partner if s["account"] == "receivable" and settings["customer_as_sub"] and partner else a.get("sub", "")
        name = a["name"] + (f"({a['code']})" if settings["software"] == "kanjo_bugyo" and a.get("code") else "")
        return {"account": name, "sub": sub, "tax": labels.get(s["tax"]) or "(未設定)",
                "amount": s["amount"], "tax_amount": s["tax_amount"]}

    return [{**e, "lines": [{"debit": one(l["debit"], e["partner"]), "credit": one(l["credit"], e["partner"])}
                            for l in e["lines"]]} for e in entries]


def _check_period(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "期間の終わりは、始まりより後の日付にしてください")
    if (date_to - date_from).days > 400:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "期間は1年以内にしてください")


def _problem(settings: dict) -> str | None:
    try:
        render(settings["software"], [], settings)
    except SettingsProblem as e:
        return str(e)
    return None


@router.get("/pending", response_model=PreviewOut)
def preview(date_from: date, date_to: date, conn: TenantConn):
    _check_period(date_from, date_to)
    settings = load_settings(conn)
    entries, before = pending_entries(conn, date_from, date_to)
    return PreviewOut(
        software=settings["software"], software_name=SOFTWARE_NAMES[settings["software"]],
        entries=_resolve(entries, settings), before_count=before, problem=_problem(settings),
    )


class ExportIn(BaseModel):
    date_from: date
    date_to: date


class ExportOut(BaseModel):
    id: UUID
    software: Software
    software_name: str
    date_from: date
    date_to: date
    entry_count: int
    created_at: datetime
    is_latest: bool


def _exports(conn: Connection, export_id: UUID | None = None) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT id, software, date_from, date_to, entry_count, created_at,
               created_at = max(created_at) OVER () AS is_latest
        FROM app.journal_exports
        ORDER BY created_at DESC
        {"" if export_id else "LIMIT 30"}
        """
    ).fetchall()
    rows = [{**r, "software_name": SOFTWARE_NAMES[r["software"]]} for r in rows]
    return [r for r in rows if r["id"] == export_id] if export_id else rows


@router.post("/exports", response_model=ExportOut, status_code=status.HTTP_201_CREATED)
def create_export(body: ExportIn, conn: TenantConn):
    """期間内の未出力の仕訳を「出力済み」として記録する。CSV は /exports/{id}/file から受け取る。"""
    _check_period(body.date_from, body.date_to)
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('journal_export:' || app.current_tenant_id()::text))")
    settings = load_settings(conn)
    problem = _problem(settings)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    entries, _ = pending_entries(conn, body.date_from, body.date_to)
    if not entries:
        raise HTTPException(status.HTTP_409_CONFLICT, "この期間に、まだ出力していない仕訳はありません")

    export_id = conn.execute(
        """
        INSERT INTO app.journal_exports (tenant_id, software, date_from, date_to, entry_count, created_by)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, app.current_user_id()) RETURNING id
        """,
        (settings["software"], body.date_from, body.date_to, len(entries)),
    ).fetchone()["id"]
    for e in entries:
        conn.execute(
            """
            INSERT INTO app.journal_export_items
              (tenant_id, export_id, source_type, source_id, kind, entry_date, summary, partner, lines)
            VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (export_id, e["source_type"], e["source_id"], e["kind"], e["date"], e["summary"], e["partner"],
             Jsonb(e["lines"])),
        )
    return _exports(conn, export_id)[0]


@router.get("/exports", response_model=list[ExportOut])
def list_exports(conn: TenantConn):
    return _exports(conn)


@router.get("/exports/{export_id}/file")
def download_export(export_id: UUID, conn: TenantConn):
    """出力した仕訳の CSV。勘定科目・税区分は今の設定で置き換える(設定を直して出し直せる)。"""
    export = conn.execute("SELECT software FROM app.journal_exports WHERE id = %s", (export_id,)).fetchone()
    if export is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "出力が見つかりません")
    items = conn.execute(
        """
        SELECT entry_date, summary, partner, lines, kind, source_type FROM app.journal_export_items
        WHERE export_id = %s ORDER BY entry_date, kind, source_type, summary
        """,
        (export_id,),
    ).fetchall()
    entries = [{"date": i["entry_date"].isoformat(), "summary": i["summary"], "partner": i["partner"],
                "lines": i["lines"]} for i in items]
    settings = load_settings(conn)
    try:
        data, filename, replaced = render(export["software"], entries, settings)
    except SettingsProblem as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    activity.record(conn, "download_journal", f"仕訳CSV「{filename}」({len(entries)}件)", "journal_export", export_id)
    return Response(
        content=data,
        media_type="text/csv; charset=shift_jis",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
            "X-Replaced-Characters": str(replaced),  # Shift_JIS にない文字を「?」にした数
            "Access-Control-Expose-Headers": "X-Replaced-Characters, Content-Disposition",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/exports/{export_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_export(export_id: UUID, conn: TenantConn):
    """最後の出力を取り消す(会計ソフトに取り込む前に、設定や期間を直して出し直すため)。"""
    latest = conn.execute(
        "SELECT id FROM app.journal_exports ORDER BY created_at DESC LIMIT 1 FOR UPDATE"
    ).fetchone()
    if latest is None or latest["id"] != export_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "取り消せるのは最後の出力だけです。それより前の出力は、会計ソフトに取り込んだあとの可能性があるため取り消せません",
        )
    conn.execute("DELETE FROM app.journal_exports WHERE id = %s", (export_id,))
