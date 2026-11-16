"""環境変数から読み込む設定。接続先や鍵はコードに書かず、環境ごとに渡す。"""

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 例: postgresql://api_login:api_login_dev@db:5432/postgres
    database_url: str

    # dev: 開発用。DEV_USER_ID の利用者としてログインしている扱いにする(docker-compose.yml で指定)
    # supabase: Supabase Auth のトークンを検証する(未実装のため、いまはすべての操作を拒否する)
    # 設定を忘れたまま公開しても開発用ログインで動かないよう、既定は supabase にしておく
    auth_mode: Literal["dev", "supabase"] = "supabase"
    dev_user_id: UUID | None = None

    # ビルドした画面の置き場所。本番イメージでは FastAPI から画面も配信する
    static_dir: Path | None = None

    # 発行した書類の控えPDFの保存先(開発ではコンテナのボリューム)
    document_dir: Path = Path("/data/documents")


settings = Settings()
