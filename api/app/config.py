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

    # AI による注文書の取り込み。ai_provider が空なら機能ごと使えない
    #   fake: 見本の注文書(docs/samples/orders)にだけ決まった答えを返す。鍵なしで画面を試せる
    #   openai_compatible: OpenAI 互換の API(Ollama・Gemini の互換エンドポイントなど)
    ai_provider: Literal["", "fake", "openai_compatible"] = ""
    ai_model: str = ""
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    # 構造化出力の受け取り方。function_calling が多くの互換 API で使える
    ai_structured_method: Literal["function_calling", "json_schema", "json_mode"] = "function_calling"
    ai_timeout_seconds: float = 60
    ai_max_pages: int = 10

    # アプリのログ(app/logs.py)。json: 1行1件の JSON(ログの保管先で検索しやすい) / text: 開発中に読みやすい形
    log_format: Literal["json", "text"] = "json"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


settings = Settings()
