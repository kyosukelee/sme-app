"""「見た・持ち出した」記録(activity_logs)。

データの変更は DB のトリガーが変更履歴(audit_logs)に残すが、閲覧やダウンロードは DB の行を変えないため、
API で明示的に記録する。対象は、外に持ち出せる・個人や取引の情報を含むものに絞る。
  ・データ一式の書き出し、仕訳CSV のダウンロード
  ・発行した書類の控え(PDF)、申請の添付、取り込んだ注文書の PDF を開いた
下書きのプレビュー(まだ発行していない書類)は記録しない。
"""

import logging
from uuid import UUID

from psycopg import Connection

from app import logs

# 画面に出す名前。ここにない操作は記録できない(書き間違いを防ぐ)
ACTIONS = {
    "export_all": "データ一式の書き出し",
    "download_journal": "仕訳CSVのダウンロード",
    "open_document": "発行した書類の控えを開いた",
    "open_attachment": "申請の添付を開いた",
    "open_import_pdf": "取り込んだ注文書を開いた",
}


def record(conn: Connection, action: str, summary: str, target_type: str | None = None, target_id: UUID | None = None) -> None:
    if action not in ACTIONS:
        raise ValueError(f"記録できない操作です: {action}")
    conn.execute(
        """
        INSERT INTO app.activity_logs (tenant_id, actor_id, action, target_type, target_id, summary, request_id)
        VALUES (app.current_tenant_id(), app.current_user_id(), %s, %s, %s, %s, %s)
        """,
        (action, target_type, target_id, summary[:300], logs.request_id()),
    )
    logs.log(logging.INFO, "activity", action=action, target_type=target_type, target_id=target_id)
