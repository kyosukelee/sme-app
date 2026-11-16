"""DB のエラーを、画面に返せる HTTP エラーに変える。"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from psycopg import errors


def _message(exc: errors.Error) -> str:
    diag = exc.diag.message_primary
    return diag or str(exc).splitlines()[0]


def register(app: FastAPI) -> None:
    @app.exception_handler(errors.RaiseException)
    async def business_rule(_req: Request, exc: errors.RaiseException):
        # トリガーが拒否した操作(発行済みの書類の変更など)。理由はそのまま画面に出す
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": _message(exc)})

    @app.exception_handler(errors.ForeignKeyViolation)
    async def missing_reference(_req: Request, _exc: errors.ForeignKeyViolation):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "指定した取引先などが見つかりません"},
        )

    @app.exception_handler(errors.InsufficientPrivilege)
    async def forbidden(_req: Request, _exc: errors.InsufficientPrivilege):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "この操作は許可されていません"})
