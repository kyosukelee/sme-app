"""操作の履歴(管理者だけ)。

- 変更の履歴: DB のトリガーが残した audit_logs を、画面で読める形(どの書類の・どの項目が・何から何に)にして返す
- 閲覧・書き出しの記録: activity_logs(控えや添付を開いた、データを持ち出した)

どちらも新しい順に、before_id より前を limit 件ずつ返す(「さらに読み込む」用)。
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from psycopg import Connection
from pydantic import BaseModel

from app.activity import ACTIONS
from app.auth import AdminConn

router = APIRouter(prefix="/api/logs", tags=["logs"])

TABLE_LABELS = {
    "quotes": "見積",
    "quote_lines": "見積の明細",
    "orders": "受注",
    "order_lines": "受注の明細",
    "invoices": "請求書",
    "invoice_lines": "請求書の明細",
    "payments": "入金の割り当て",
    "receipts": "入金",
    "customers": "取引先",
    "items": "商品・サービス",
    "company_profiles": "自社情報",
    "tenant_settings": "会社の設定",
    "memberships": "利用者の権限",
    "member_profiles": "社員",
    "approver_groups": "担当役割",
    "approver_group_members": "担当役割の社員",
    "request_types": "申請種別",
    "route_steps": "承認の段",
    "requests": "申請",
    "request_attachments": "申請の添付",
    "bank_formats": "入金消込の取込形式",
    "bank_imports": "銀行明細の取り込み",
    "bank_statements": "銀行の入金明細",
    "journal_settings": "仕訳の設定",
    "journal_exports": "仕訳の出力",
    "document_imports": "注文書の取り込み",
}

FIELD_LABELS = {
    "name": "名前", "title": "件名", "status": "状態", "remarks": "備考", "memo": "メモ",
    "quote_number": "見積番号", "order_number": "受注番号", "invoice_number": "請求書番号", "request_number": "申請番号",
    "version": "版", "customer_name": "取引先", "customer_honorific": "敬称", "customer_address": "取引先の住所",
    "customer_id": "取引先", "issue_date": "発行日", "valid_until": "有効期限", "order_date": "受注日",
    "delivery_due_date": "納期", "due_date": "支払期限", "closing_date": "締め日", "issued_at": "発行日時",
    "voided_at": "取消日時", "superseded_at": "訂正日時",
    "subtotal_amount": "小計", "tax_amount": "消費税", "total_amount": "合計", "amount": "金額",
    "withholding": "源泉徴収", "withholding_amount": "源泉徴収税額", "carried_over_amount": "繰越額",
    "fee_amount": "振込手数料", "paid_on": "入金日", "received_on": "入金日", "kind": "種類",
    "line_no": "行", "description": "品名", "quantity": "数量", "unit": "単位", "unit_price": "単価",
    "tax_category": "税区分", "tax_rate": "税率", "transaction_date": "取引日", "delivered_on": "納品日",
    "date_confirmed": "取引日の確定", "customer_order_ref": "お客様の注文番号",
    "code": "コード", "is_active": "使用中", "honorific": "敬称", "postal_code": "郵便番号", "address": "住所",
    "phone": "電話番号", "email": "メールアドレス", "contact_name": "担当者", "payer_names": "振込名義",
    "document_names": "書類上の名前", "withholding_default": "源泉徴収(既定)",
    "closing_day": "締め日", "payment_month_offset": "支払月", "payment_day": "支払日",
    "registration_number": "登録番号", "bank_account": "振込先", "business_type": "事業形態",
    "representative_name": "代表者名", "tax_exempt": "免税事業者",
    "rounding_mode": "端数処理", "numbering_period": "採番の区切り", "number_digits": "連番の桁数",
    "quote_prefix": "見積番号の頭", "order_prefix": "受注番号の頭", "invoice_prefix": "請求書番号の頭",
    "request_prefix": "申請番号の頭", "quote_valid_days": "見積の有効日数", "remarks_template": "備考の定型文",
    "layout": "書類のレイアウト", "bank_fee_limit": "振込手数料の上限", "ai_import_enabled": "AIによる取り込み",
    "role": "権限", "user_id": "社員", "group_id": "担当役割", "display_name": "氏名", "department": "部署",
    "manager_user_id": "上長",
    "amount_mode": "金額の入力", "amount_label": "金額の名前", "sort_order": "並び順",
    "form_schema": "入力項目", "step_no": "段", "approver_kind": "承認者の指定", "approver_group_id": "担当役割",
    "approver_user_id": "承認者", "min_amount": "金額の条件", "form_data": "申請の内容", "round": "申請の回数",
    "current_step": "承認待ちの段", "applicant_id": "申請者", "request_type_id": "申請種別",
    "submitted_at": "申請日時", "decided_at": "完了日時", "file_name": "ファイル名", "content_type": "ファイルの種類",
    "size_bytes": "大きさ", "software": "会計ソフト", "sales_date_basis": "売上の日付", "customer_as_sub": "補助科目に取引先",
    "accounts": "勘定科目", "tax_labels": "税区分の名前", "date_from": "期間の始まり", "date_to": "期間の終わり",
    "entry_count": "仕訳の数", "encoding": "文字コード", "skip_rows": "読み飛ばす行", "filename": "ファイル名",
    "imported_count": "取り込んだ件数", "duplicate_count": "重複の件数", "txn_date": "取引日", "payer_name": "振込名義",
    "receipt_id": "消込先の入金", "ignored": "対象外", "ignore_reason": "対象外の理由",
    "discard_reason": "破棄の理由", "error_message": "エラー",
}

# 画面に出さない列(ID・日時の自動更新・中身が大きい・照合用の値)
HIDDEN = {
    "id", "tenant_id", "created_at", "updated_at", "storage_path", "sha256", "fingerprint", "payer_key",
    "extracted", "draft", "checks", "logo_path", "seal_path", "previous_version_id", "quote_id", "order_id",
    "invoice_id", "request_id", "import_id", "format_id", "item_id", "quote_line_id", "order_line_id",
    "source_type", "source_id", "source_import_id", "created_by", "confirmed_by", "uploaded_by",
    "issuer_name", "issuer_registration_number", "issuer_representative", "issuer_tax_exempt",
}

USER_FIELDS = {"user_id", "manager_user_id", "approver_user_id", "applicant_id"}
GROUP_FIELDS = {"group_id", "approver_group_id"}

VALUE_LABELS = {
    "role": {"admin": "管理者", "member": "一般"},
    "rounding_mode": {"floor": "切り捨て", "half_up": "四捨五入", "ceil": "切り上げ"},
    "numbering_period": {"year": "年ごと", "month": "月ごと"},
    "tax_category": {"standard": "10%", "reduced": "8%(軽減)", "exempt": "非課税", "non_taxable": "不課税", "export": "免税"},
    "approver_kind": {"manager": "申請者の上長", "group": "担当役割", "user": "特定の社員"},
    "business_type": {"corporation": "法人", "sole_proprietor": "個人事業主"},
    "amount_mode": {"required": "必須", "optional": "任意", "none": "なし"},
    "payment_month_offset": {"0": "当月", "1": "翌月", "2": "翌々月", "3": "3か月後"},
}
LINE_TABLES = ("quote_lines", "order_lines", "invoice_lines")
STATUS_LABELS = {
    "quotes": {"draft": "作成中", "issued": "発行済", "ordered": "受注済", "lost": "失注"},
    "orders": {"open": "未請求あり", "billed": "請求済"},
    "invoices": {"draft": "作成中", "issued": "発行済", "paid": "入金済", "void": "取消", "superseded": "訂正済"},
    "document_imports": {"queued": "読み取り待ち", "processing": "読み取り中", "review": "確認待ち", "failed": "失敗",
                         "confirmed": "確定済", "discarded": "破棄"},
    "requests": {"draft": "下書き", "pending": "承認待ち", "returned": "差戻し", "approved": "承認済",
                 "rejected": "却下", "withdrawn": "取下げ"},
}

ACTION_LABELS = {"insert": "追加", "update": "変更", "delete": "削除"}


class Change(BaseModel):
    field: str
    label: str
    before: str | None
    after: str | None


class ChangeLog(BaseModel):
    id: int
    created_at: Any
    actor_id: UUID | None
    actor_name: str | None
    table_name: str
    table_label: str
    record_id: UUID
    record_label: str
    action: str
    action_label: str
    changes: list[Change]
    request_id: str | None


class ChangePage(BaseModel):
    items: list[ChangeLog]
    next_before_id: int | None


class ActivityLog(BaseModel):
    id: int
    created_at: Any
    actor_id: UUID | None
    actor_name: str | None
    action: str
    action_label: str
    summary: str
    target_type: str | None
    target_id: UUID | None
    request_id: str | None


class ActivityPage(BaseModel):
    items: list[ActivityLog]
    next_before_id: int | None


class Option(BaseModel):
    value: str
    label: str


class FiltersOut(BaseModel):
    tables: list[Option]
    actions: list[Option]


@router.get("/filters", response_model=FiltersOut)
def filters(conn: AdminConn):
    return FiltersOut(
        tables=[Option(value=k, label=v) for k, v in TABLE_LABELS.items()],
        actions=[Option(value=k, label=v) for k, v in ACTIONS.items()],
    )


def _period(where: list[str], params: list, date_from: date | None, date_to: date | None) -> None:
    # 日付は日本時間の1日として扱う
    if date_from:
        where.append("l.created_at >= (%s::date)::timestamp AT TIME ZONE 'Asia/Tokyo'")
        params.append(date_from)
    if date_to:
        where.append("l.created_at < ((%s::date + 1)::timestamp AT TIME ZONE 'Asia/Tokyo')")
        params.append(date_to)


def _names(conn: Connection) -> tuple[dict[str, str], dict[str, str]]:
    people = {str(r["user_id"]): r["display_name"] for r in conn.execute(
        "SELECT user_id, display_name FROM app.member_profiles WHERE tenant_id = app.current_tenant_id()").fetchall()}
    groups = {str(r["id"]): r["name"] for r in conn.execute("SELECT id, name FROM app.approver_groups").fetchall()}
    return people, groups


def _number(d: Decimal) -> str:
    """金額・数量の表示(480000.00 → 480,000、1.500 → 1.5)。"""
    return f"{int(d):,}" if d == d.to_integral_value() else f"{d.normalize():,f}"


def _text(value: Any, limit: int = 200) -> str:
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _format(table: str, field: str, value: Any, people: dict, groups: dict) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if field in USER_FIELDS:
        return people.get(str(value), "(見つからない社員)")
    if field in GROUP_FIELDS:
        return groups.get(str(value), "(削除された担当役割)")
    if field == "status" and table in STATUS_LABELS:
        return STATUS_LABELS[table].get(value, value)
    if field in VALUE_LABELS:
        return VALUE_LABELS[field].get(str(value), str(value))
    if field == "tax_rate" and isinstance(value, Decimal):
        return f"{_number(value * 100)}%"
    if field in ("closing_day", "payment_day") and isinstance(value, Decimal):
        return "月末" if value == 31 else f"{int(value)}日"
    if field == "form_schema":
        return "、".join(f.get("label", "") for f in value) if isinstance(value, list) else _text(value)
    return _text(value)


def record_label(table: str, v: dict, people: dict, groups: dict) -> str:
    """変更された対象を、人が見て分かる名前にする(書類番号・名前など)。"""
    def first(*keys):
        return next((str(v[k]) for k in keys if v.get(k) not in (None, "")), "")

    if table == "quotes":
        return f"{first('quote_number') or '(作成中)'} {first('title', 'customer_name')}".strip()
    if table == "orders":
        return f"{first('order_number')} {first('title', 'customer_name')}".strip()
    if table == "invoices":
        number = first("invoice_number") or "(作成中)"
        return f"{number} 第{v.get('version', 1)}版 {first('customer_name')}".strip()
    if table in ("quote_lines", "order_lines", "invoice_lines"):
        return f"{v.get('line_no', '')}行目 {first('description')}".strip()
    if table == "payments":
        return f"{first('paid_on')} {_text(v.get('amount'))}円"
    if table == "receipts":
        return f"{first('received_on')} {_text(v.get('amount'))}円"
    if table == "requests":
        return f"{first('request_number') or '(下書き)'} {first('title')}".strip()
    if table == "member_profiles":
        return first("display_name")
    if table == "memberships":
        return people.get(str(v.get("user_id")), "(社員)")
    if table == "approver_group_members":
        return f"{groups.get(str(v.get('group_id')), '担当役割')} ・ {people.get(str(v.get('user_id')), '(社員)')}"
    if table == "route_steps":
        return f"{v.get('step_no', '')}段目"
    if table == "tenant_settings":
        return "会社の設定"
    if table == "company_profiles":
        return "自社情報"
    if table == "journal_settings":
        return "仕訳の設定"
    if table == "journal_exports":
        return f"{first('date_from')}〜{first('date_to')}"
    if table == "bank_statements":
        return f"{first('txn_date')} {first('payer_name')} {_text(v.get('amount'))}円"
    return first("name", "display_name", "title", "file_name", "filename", "description") or TABLE_LABELS.get(table, table)


def diff(table: str, action: str, old: dict | None, new: dict | None, people: dict, groups: dict) -> list[Change]:
    """変わった項目の一覧。追加・削除は中身(空でない項目)を、変更は変わった項目だけを返す。"""
    old, new = old or {}, new or {}
    keys = list(dict.fromkeys([*old.keys(), *new.keys()]))
    changes = []
    for k in keys:
        if k in HIDDEN:
            continue
        if action == "update" and old.get(k) == new.get(k):
            continue
        before = _format(table, k, old.get(k), people, groups) if action != "insert" else None
        after = _format(table, k, new.get(k), people, groups) if action != "delete" else None
        if before is None and after is None:
            continue
        label = "説明" if k == "description" and not table.endswith("_lines") else FIELD_LABELS.get(k, k)
        changes.append(Change(field=k, label=label, before=before, after=after))
    return changes[:40]


@router.get("/changes", response_model=ChangePage)
def list_changes(
    conn: AdminConn,
    table: str | None = None,
    actor_id: UUID | None = None,
    record_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    before_id: int | None = None,
    include_lines: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    """明細の行(見積・受注・請求書の明細)は保存のたびに入れ替わって数が多いので、
    対象や記録を指定したとき・include_lines のときだけ出す。書類そのものの変更(合計など)は常に出る。"""
    if table is not None and table not in TABLE_LABELS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "対象の指定が正しくありません")
    # 何も変わっていない更新(この仕組みを入れる前の記録に残っている)は出さない
    where = ["l.table_name = ANY(%s)",
             "(l.action <> 'update' OR (l.old_values - 'updated_at') IS DISTINCT FROM (l.new_values - 'updated_at'))"]
    params: list = [list(TABLE_LABELS)]
    if table:
        where.append("l.table_name = %s")
        params.append(table)
    elif not include_lines and not record_id:
        where.append("NOT (l.table_name = ANY(%s))")
        params.append(list(LINE_TABLES))
    if actor_id:
        where.append("l.actor_id = %s")
        params.append(actor_id)
    if record_id:
        where.append("l.record_id = %s")
        params.append(record_id)
    if before_id:
        where.append("l.id < %s")
        params.append(before_id)
    _period(where, params, date_from, date_to)
    rows = conn.execute(
        f"""
        SELECT l.id, l.created_at, l.actor_id, l.table_name, l.record_id, l.action,
               l.old_values::text AS old_values, l.new_values::text AS new_values, l.request_id
        FROM app.audit_logs l
        WHERE {' AND '.join(where)}
        ORDER BY l.id DESC LIMIT %s
        """,
        [*params, limit + 1],
    ).fetchall()
    people, groups = _names(conn)
    # 金額の誤差を出さないよう、数値は Decimal で読む
    load = lambda v: json.loads(v, parse_float=Decimal, parse_int=Decimal) if v else None  # noqa: E731
    rows = [r | {"old_values": load(r["old_values"]), "new_values": load(r["new_values"])} for r in rows]
    items = [
        ChangeLog(
            id=r["id"], created_at=r["created_at"], actor_id=r["actor_id"],
            actor_name=people.get(str(r["actor_id"])) if r["actor_id"] else None,
            table_name=r["table_name"], table_label=TABLE_LABELS[r["table_name"]], record_id=r["record_id"],
            record_label=record_label(r["table_name"], r["new_values"] or r["old_values"] or {}, people, groups),
            action=r["action"], action_label=ACTION_LABELS[r["action"]],
            changes=diff(r["table_name"], r["action"], r["old_values"], r["new_values"], people, groups),
            request_id=r["request_id"],
        )
        for r in rows[:limit]
    ]
    return ChangePage(items=items, next_before_id=items[-1].id if len(rows) > limit else None)


@router.get("/activities", response_model=ActivityPage)
def list_activities(
    conn: AdminConn,
    action: str | None = None,
    actor_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    before_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    if action is not None and action not in ACTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "操作の指定が正しくありません")
    where, params = ["true"], []
    if action:
        where.append("l.action = %s")
        params.append(action)
    if actor_id:
        where.append("l.actor_id = %s")
        params.append(actor_id)
    if before_id:
        where.append("l.id < %s")
        params.append(before_id)
    _period(where, params, date_from, date_to)
    rows = conn.execute(
        f"""
        SELECT l.*, p.display_name AS actor_name
        FROM app.activity_logs l
        LEFT JOIN app.member_profiles p ON p.tenant_id = l.tenant_id AND p.user_id = l.actor_id
        WHERE {' AND '.join(where)}
        ORDER BY l.id DESC LIMIT %s
        """,
        [*params, limit + 1],
    ).fetchall()
    items = [
        ActivityLog(
            id=r["id"], created_at=r["created_at"], actor_id=r["actor_id"], actor_name=r["actor_name"],
            action=r["action"], action_label=ACTIONS.get(r["action"], r["action"]), summary=r["summary"],
            target_type=r["target_type"], target_id=r["target_id"], request_id=r["request_id"],
        )
        for r in rows[:limit]
    ]
    return ActivityPage(items=items, next_before_id=items[-1].id if len(rows) > limit else None)
