"""アプリのログ(標準出力に1行1件の JSON で出す)。

- リクエストごとに「リクエストID」を振り、どの会社の誰が・どの API を呼び・結果はどうだったかを1行で残す
- 想定外のエラーは例外の内容(トレースバック)ごと残し、画面にはリクエストID だけを返す
  (問い合わせのときに、ログの該当行をすぐに探せるように)
- 同じリクエストID を DB の変更履歴(audit_logs)と操作の記録(activity_logs)にも残すので、突き合わせられる

本文(入力内容)・パスワード・トークンはログに出さない。出すのは ID・API の形・状態・所要時間だけ。
ログの保管先(Cloud Logging・Loki など)は動かす環境で決める。コンテナは標準出力に書くだけ。
"""

import json
import logging
import re
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger("sme")

# リクエストの情報。依存関係(auth.py)で利用者と会社が分かったら書き足す。
# ミドルウェアと API の処理で同じ dict を共有するので、後から分かった値も最後のログに載る
_context: ContextVar[dict | None] = ContextVar("request_context", default=None)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def context() -> dict:
    ctx = _context.get()
    return ctx if ctx is not None else {}


def request_id() -> str | None:
    return context().get("request_id")


def set_identity(*, user_id=None, tenant_id=None) -> None:
    ctx = _context.get()
    if ctx is None:
        return
    if user_id is not None:
        ctx["user_id"] = str(user_id)
    if tenant_id is not None:
        ctx["tenant_id"] = str(tenant_id)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if not hasattr(record, "fields"):  # このアプリ以外のログ
            entry.update({k: v for k, v in context().items() if k in _IDS})
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["error"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        return json.dumps(entry, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """開発中に読みやすい形(LOG_FORMAT=text)。"""

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        line = f"{record.levelname:<7} {record.getMessage()} " + " ".join(f"{k}={v}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info))
        return line.rstrip()


def configure() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(TextFormatter() if settings.log_format == "text" else JsonFormatter())
    logger.handlers[:] = [handler]
    logger.setLevel(settings.log_level)
    logger.propagate = False
    # リクエストの記録はこのアプリで出すので、uvicorn の同じ内容のアクセスログは止める
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


_IDS = ("request_id", "tenant_id", "user_id")


def log(level: int, message: str, exc_info: bool = False, **fields) -> None:
    """ログを1件出す。いまのリクエストの番号・会社・利用者を、出した時点の値で付ける。"""
    ids = {k: v for k, v in context().items() if k in _IDS}
    logger.log(level, message, exc_info=exc_info, extra={"fields": ids | fields})


def _route(request: Request) -> str:
    """ログに出す API の形。/api/quotes/1234… ではなく /api/quotes/{quote_id} にする(ID を並べないため)。"""
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_log(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        ctx = {"request_id": rid}
        token = _context.set(ctx)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 想定外のエラー。内容はログにだけ残す
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                log(logging.ERROR, "unhandled error", exc_info=True,
                    method=request.method, path=_route(request), status=500, duration_ms=elapsed)
                response = JSONResponse(
                    status_code=500,
                    content={
                        "detail": f"サーバーでエラーが発生しました。お問い合わせの際は、この番号をお伝えください: {rid}",
                        "request_id": rid,
                    },
                )
            else:
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                path = _route(request)
                code = response.status_code
                level = logging.DEBUG if path == "/api/health" else logging.INFO
                if code in (401, 403):
                    level = logging.WARNING  # 権限のない操作の試み
                elif code >= 500:
                    level = logging.ERROR
                if path.startswith("/api/"):
                    log(level, "request", method=request.method, path=path, status=code, duration_ms=elapsed)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            _context.reset(token)
