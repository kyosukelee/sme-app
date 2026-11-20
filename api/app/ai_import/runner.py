"""取り込み1件分の処理(アップロードの応答を返したあとに裏で動かす)。

DB のトランザクションは3回に分け、AI を待つあいだは開けたままにしない。
  1. 状態を「処理中」にして確定する
  2. PDF を読み、AI で種類の判定と項目の抽出をする(DB に触らない)
  3. 結果と下書き・検証結果を書き込み、「要確認」(失敗なら「失敗」)にする
会社ごとのデータ分離(RLS)が効くよう、取り込んだ利用者と会社で接続する。
"""

import json
import logging
import time
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app import storage
from app.ai_import import review
from app.ai_import.graph import read_order
from app.ai_import.pdf_text import PdfReadError, read_pdf
from app.ai_import.providers import PROMPT_VERSION, ProviderError, get_provider
from app.config import settings
from app.db import scoped_transaction

log = logging.getLogger(__name__)

# 処理中のまま、これより長く止まっているものは中断されたとみなす(API の再起動など)
STALE_MINUTES = 15


def fail_stale(conn: Connection) -> None:
    conn.execute(
        f"""
        UPDATE app.document_imports
        SET status = 'failed', error_message = '処理が途中で止まりました。「やり直す」を押してください'
        WHERE status IN ('queued', 'processing') AND updated_at < now() - interval '{STALE_MINUTES} minutes'
        """
    )


def process_import(import_id: UUID, tenant_id: UUID, user_id: UUID, skip_classify: bool = False) -> None:
    # 1. 処理中にする
    with scoped_transaction(user_id, tenant_id) as conn:
        row = conn.execute(
            "SELECT status, storage_path FROM app.document_imports WHERE id = %s FOR UPDATE", (import_id,)
        ).fetchone()
        if row is None or row["status"] != "queued":
            return
        conn.execute("UPDATE app.document_imports SET status = 'processing' WHERE id = %s", (import_id,))
        path = row["storage_path"]

    # 2. 読み取り(DB に触らない)
    started = time.monotonic()
    page_count = None
    result = None
    provider_name = model = None
    try:
        text = read_pdf(storage.read(path), settings.ai_max_pages)
        page_count = text.page_count
        provider = get_provider()
        provider_name, model = provider.name, provider.model
        result = read_order(provider, text.for_prompt(), skip_classify=skip_classify)
        error = result.error
    except (PdfReadError, ProviderError) as e:
        error = str(e)
    except Exception:  # 想定していない失敗。詳しくはログに残し、画面には一般的な文を出す
        log.exception("注文書の取り込みに失敗しました: %s", import_id)
        error = "読み取りの途中でエラーが発生しました。もう一度やり直してください"
    duration_ms = round((time.monotonic() - started) * 1000)

    # 3. 結果を書き込む
    with scoped_transaction(user_id, tenant_id) as conn:
        common = {
            "id": import_id,
            "page_count": page_count,
            "provider": provider_name,
            "model": model,
            "duration_ms": duration_ms,
            "input_tokens": result.input_tokens if result else None,
            "output_tokens": result.output_tokens if result else None,
        }
        if error or result is None or result.order is None:
            conn.execute(
                """
                UPDATE app.document_imports
                SET status = 'failed', error_message = %(error)s, page_count = %(page_count)s,
                    provider = %(provider)s, model = %(model)s, duration_ms = %(duration_ms)s,
                    input_tokens = %(input_tokens)s, output_tokens = %(output_tokens)s
                WHERE id = %(id)s
                """,
                {**common, "error": error or "読み取れませんでした"},
            )
            return

        order = result.order.model_dump()
        extracted = {"classification": result.classification.model_dump(), "order": order}
        draft = review.initial_draft(conn, order)
        checks = review.evaluate(conn, order, draft)
        conn.execute(
            """
            UPDATE app.document_imports
            SET status = 'review', extracted = %(extracted)s, draft = %(draft)s, checks = %(checks)s,
                extract_method = 'text', page_count = %(page_count)s, provider = %(provider)s, model = %(model)s,
                prompt_version = %(prompt_version)s, duration_ms = %(duration_ms)s,
                input_tokens = %(input_tokens)s, output_tokens = %(output_tokens)s, error_message = NULL
            WHERE id = %(id)s
            """,
            {
                **common,
                "extracted": Jsonb(extracted),
                "draft": Jsonb(json.loads(json.dumps(draft, default=str))),
                "checks": Jsonb(checks),
                "prompt_version": PROMPT_VERSION,
            },
        )
