"""データの一括エクスポート(解約時・乗り換え時の持ち出し用)。

選んだ会社のデータをすべて、1つの ZIP ファイルにまとめて返す。管理者だけが使える。

- csv/     … テーブルごとの CSV(UTF-8 BOM 付き。Excel でそのまま開ける)
- 控えPDF/ … 発行した見積書・注文請書・請求書の控え(版ごと)
- manifest.csv … 入っているファイルの一覧と SHA-256。控えPDFは発行時のハッシュ値とも照らし合わせる
- はじめにお読みください.txt … 中身の説明

電子帳簿保存法の保存期間(原則7年)のあいだは、解約後もこのファイルを保存しておく必要がある。
データはメモリ上で組み立てる(中小企業の数年分の量を想定)。
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from app import activity, storage
from app.auth import AdminConn
from app.clock import today

router = APIRouter(prefix="/api/export", tags=["export"])

# (テーブル名, 日本語の説明, 並び順)。外部キーの親から順に並べる
TABLES: list[tuple[str, str, str]] = [
    ("tenants", "会社", "id"),
    ("company_profiles", "自社情報(書類に印字する内容)", "tenant_id"),
    ("tenant_settings", "会社の設定(端数処理・締め日・支払日)", "tenant_id"),
    ("memberships", "利用者と権限", "created_at, user_id"),
    ("member_profiles", "社員(氏名・部署・上長)", "created_at, user_id"),
    ("approver_groups", "担当役割(経理担当・代表など)", "name"),
    ("approver_group_members", "担当役割に属する社員", "group_id, user_id"),
    ("request_types", "申請種別(入力項目の定義)", "sort_order, name"),
    ("route_steps", "申請種別ごとの承認の段", "request_type_id, step_no"),
    ("customers", "取引先", "created_at, id"),
    ("items", "商品・サービス", "created_at, id"),
    ("quotes", "見積", "created_at, id"),
    ("quote_lines", "見積の明細", "quote_id, line_no"),
    ("quote_tax_summaries", "見積の税率ごとの合計", "quote_id, tax_rate DESC"),
    ("document_imports", "注文書の取り込み(AIの読み取り結果と確認の状態)", "created_at, id"),
    ("document_import_corrections", "取り込みで人が直した項目(AIの値と確定した値)", "import_id, id"),
    ("orders", "受注", "created_at, id"),
    ("order_lines", "受注の明細", "order_id, line_no"),
    ("order_tax_summaries", "受注の税率ごとの合計", "order_id, tax_rate DESC"),
    ("invoices", "請求書", "created_at, id"),
    ("invoice_lines", "請求書の明細", "invoice_id, line_no"),
    ("invoice_tax_summaries", "請求書の税率ごとの合計", "invoice_id, tax_rate DESC"),
    ("invoice_carryovers", "締め請求の繰越の内訳", "invoice_id, source_issue_date"),
    ("receipts", "入金(まとめ入金)", "created_at, id"),
    ("payments", "入金の請求書への割り当て", "created_at, id"),
    ("bank_formats", "入金消込の取込形式(銀行CSVの列の対応)", "name"),
    ("bank_imports", "銀行の明細の取り込み履歴", "created_at, id"),
    ("bank_statements", "取り込んだ銀行の入金明細と消込の状態", "txn_date, created_at"),
    ("journal_settings", "仕訳の設定(会計ソフト・勘定科目・税区分)", "tenant_id"),
    ("journal_exports", "仕訳CSVの出力履歴", "created_at, id"),
    ("journal_export_items", "出力した仕訳", "entry_date, id"),
    ("requests", "申請(ワークフロー)", "created_at, id"),
    ("request_steps", "申請ごとの承認ルートと各段の結果", "request_id, round, step_no"),
    ("request_events", "申請・承認・差戻しなどの履歴", "id"),
    ("request_attachments", "申請の添付ファイルの一覧", "created_at, id"),
    ("number_sequences", "採番の状況", "doc_type, period"),
    ("issued_documents", "発行した書類の控えの一覧(ハッシュ値つき)", "issued_at, id"),
    ("audit_logs", "変更の履歴", "id"),
    ("activity_logs", "閲覧・書き出しの記録(控えや添付を開いた・データを持ち出した)", "id"),
]

DOC_FOLDERS = {"quote": "見積書", "order": "注文請書", "invoice": "請求書"}

README = """\
このファイルは「{name}」のデータを {at} に書き出したものです。

■ 中身
  csv/              テーブルごとのデータ(UTF-8 BOM 付きの CSV。Excel でそのまま開けます)
  控えPDF/          発行した見積書・注文請書・請求書の控え(訂正した請求書は版ごと)
  添付/             申請(ワークフロー)に添付した領収書などのファイル(申請番号ごと)
  取り込んだ注文書/ 「注文書から取り込む」で取り込んだPDF(破棄したものを含む)
  manifest.csv      入っているファイルの一覧と SHA-256(改ざんの確認用)

■ CSV の一覧
{tables}

■ 控えPDFの確認
  manifest.csv の「発行時のSHA-256」は発行したときに記録した値です。
  「SHA-256」と一致していれば、発行してから書き換えられていません。
  Windows では PowerShell の Get-FileHash コマンドで確かめられます。

■ 保存について
  電子取引で送った請求書などは、法人税法・電子帳簿保存法により原則7年間の保存が必要です。
  解約後もこのファイルを削除せず、日付・金額・取引先で探せる状態で保存してください。
  (この説明は一般的な内容で、個別の事情に合うことを保証するものではありません)
"""


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, Decimal, UUID)):
        return str(value)
    return str(value)


def _csv(conn, table: str, order_by: str) -> tuple[bytes, int]:
    # テーブル名と並び順はこのファイルに書いた固定値だけを使う
    where = "id = app.current_tenant_id()" if table == "tenants" else "tenant_id = app.current_tenant_id()"
    cur = conn.execute(f"SELECT * FROM app.{table} WHERE {where} ORDER BY {order_by}")
    columns = [c.name for c in cur.description]
    rows = cur.fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row[c]) for c in columns])
    return ("﻿" + buf.getvalue()).encode("utf-8"), len(rows)


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-]", "_", name)


def _safe_name(name: str) -> str:
    """添付のファイル名。日本語は残し、フォルダの区切りや使えない記号だけを置き換える。"""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:120] or "file"


def build_export(conn) -> tuple[bytes, str]:
    """ZIP の中身と、会社名を返す。"""
    tenant = conn.execute("SELECT name FROM app.tenants WHERE id = app.current_tenant_id()").fetchone()
    exported_at = datetime.now().astimezone().replace(microsecond=0)
    manifest: list[list[str]] = []
    out = io.BytesIO()

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:

        def add(path: str, data: bytes, kind: str, rows: str = "", saved_sha: str = "", note: str = ""):
            zf.writestr(path, data)
            manifest.append([path, kind, rows, hashlib.sha256(data).hexdigest(), saved_sha, note])

        for table, label, order_by in TABLES:
            data, count = _csv(conn, table, order_by)
            add(f"csv/{table}.csv", data, label, str(count))

        documents = conn.execute(
            """
            SELECT doc_type, document_number, version, storage_path, sha256
            FROM app.issued_documents
            WHERE tenant_id = app.current_tenant_id()
            ORDER BY doc_type, document_number, version
            """
        ).fetchall()
        for d in documents:
            folder = DOC_FOLDERS.get(d["doc_type"], d["doc_type"])
            path = f"控えPDF/{folder}/{_safe(d['document_number'])}_v{d['version']}.pdf"
            try:
                data = storage.read(d["storage_path"])
            except (OSError, ValueError):
                manifest.append([path, folder, "", "", d["sha256"], "控えPDFが見つかりません(書き出せませんでした)"])
                continue
            note = "" if hashlib.sha256(data).hexdigest() == d["sha256"] else "発行時のハッシュ値と一致しません"
            add(path, data, folder, saved_sha=d["sha256"], note=note)

        attachments = conn.execute(
            """
            SELECT a.storage_path, a.file_name, a.sha256, coalesce(r.request_number, '下書き_' || left(r.id::text, 8)) AS folder
            FROM app.request_attachments a JOIN app.requests r ON r.id = a.request_id
            WHERE a.tenant_id = app.current_tenant_id()
            ORDER BY r.created_at, a.created_at
            """
        ).fetchall()
        for i, a in enumerate(attachments, start=1):
            path = f"添付/{_safe(a['folder'])}/{i:04d}_{_safe_name(a['file_name'])}"
            try:
                data = storage.read(a["storage_path"])
            except (OSError, ValueError):
                manifest.append([path, "申請の添付", "", "", a["sha256"], "添付ファイルが見つかりません(書き出せませんでした)"])
                continue
            note = "" if hashlib.sha256(data).hexdigest() == a["sha256"] else "添付したときのハッシュ値と一致しません"
            add(path, data, "申請の添付", saved_sha=a["sha256"], note=note)

        imports = conn.execute(
            """
            SELECT storage_path, file_name, sha256, created_at
            FROM app.document_imports
            WHERE tenant_id = app.current_tenant_id()
            ORDER BY created_at, id
            """
        ).fetchall()
        for i, d in enumerate(imports, start=1):
            path = f"取り込んだ注文書/{i:04d}_{_safe_name(d['file_name'])}"
            try:
                data = storage.read(d["storage_path"])
            except (OSError, ValueError):
                manifest.append([path, "取り込んだ注文書", "", "", d["sha256"], "ファイルが見つかりません(書き出せませんでした)"])
                continue
            note = "" if hashlib.sha256(data).hexdigest() == d["sha256"] else "取り込んだときのハッシュ値と一致しません"
            add(path, data, "取り込んだ注文書", saved_sha=d["sha256"], note=note)

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow(["ファイル", "内容", "件数", "SHA-256", "発行時のSHA-256", "注意"])
        writer.writerows(manifest)
        zf.writestr("manifest.csv", ("﻿" + buf.getvalue()).encode("utf-8"))

        tables = "\n".join(f"  csv/{t}.csv  {label}" for t, label, _ in TABLES)
        readme = README.format(name=tenant["name"], at=exported_at.strftime("%Y年%m月%d日 %H:%M"), tables=tables)
        zf.writestr("はじめにお読みください.txt", readme.replace("\n", "\r\n").encode("utf-8-sig"))

    return out.getvalue(), tenant["name"]


@router.get("")
def export_all(conn: AdminConn):
    activity.record(conn, "export_all", "データ一式(CSV・控えPDF・添付)の書き出し")
    data, name = build_export(conn)
    filename = f"{name}_データ一式_{today():%Y%m%d}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"export_{today():%Y%m%d}.zip\"; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )
