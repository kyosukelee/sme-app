"""AI の提供元の切り替え。

どの提供元でも「書類の種類の判定」と「項目の抽出」の2つだけを受け持つ。
  ・openai_compatible: LangChain の ChatOpenAI で OpenAI 互換の API を呼ぶ。
    Ollama(手元のPC)や、Gemini などの OpenAI 互換エンドポイントを AI_BASE_URL で指定する
  ・fake: 見本の注文書にだけ決まった答えを返す(テストと、鍵なしでの画面の確認用)
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai_import.schema import Classification, ExtractedOrder
from app.config import settings

PROMPT_DIR = Path(__file__).parent / "prompts"
FAKE_DIR = Path(__file__).parent / "fake_answers"
PROMPT_VERSION = "order_v1"

T = TypeVar("T", bound=BaseModel)


class ExtractionError(Exception):
    """AI の答えが決められた形にならなかった(やり直す価値がある)。"""


class ProviderError(Exception):
    """AI を呼び出せなかった(接続・認証・時間切れなど)。メッセージは画面に出す。"""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    name: str
    model: str

    def classify(self, text: str) -> tuple[Classification, Usage]: ...

    def extract(self, text: str) -> tuple[ExtractedOrder, Usage]: ...


@cache
def prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------
# LangChain(OpenAI 互換 API)
# ---------------------------------------------------------------------
class LangChainProvider:
    def __init__(self, chat_model, *, name: str, model: str, method: str = "function_calling"):
        self._chat = chat_model
        self._method = method
        self.name = name
        self.model = model

    def _ask(self, schema: type[T], system: str, text: str) -> tuple[T, Usage]:
        from langchain_core.messages import HumanMessage, SystemMessage

        runnable = self._chat.with_structured_output(schema, method=self._method, include_raw=True)
        try:
            result = runnable.invoke([SystemMessage(system), HumanMessage(text)])
        except ValidationError as e:
            raise ExtractionError(str(e)) from e
        except Exception as e:  # 接続・認証・時間切れ・提供元のエラー
            raise ProviderError(_provider_message(e)) from e

        raw = result.get("raw")
        meta = getattr(raw, "usage_metadata", None) or {}
        usage = Usage(meta.get("input_tokens", 0), meta.get("output_tokens", 0))
        parsed = result.get("parsed")
        if parsed is None:
            raise ExtractionError(str(result.get("parsing_error") or "AI の答えを読み取れませんでした"))
        return parsed, usage

    def classify(self, text: str) -> tuple[Classification, Usage]:
        # 種類の判定は最初の方だけで足りる(料金と時間を抑える)
        return self._ask(Classification, prompt("classify_v1"), text[:4000])

    def extract(self, text: str) -> tuple[ExtractedOrder, Usage]:
        return self._ask(ExtractedOrder, prompt(PROMPT_VERSION), text)


def _provider_message(e: Exception) -> str:
    kind = type(e).__name__
    if "Timeout" in kind:
        return "AIの応答が時間内に返りませんでした。しばらくしてからやり直してください"
    if "Authentication" in kind or "PermissionDenied" in kind:
        return "AIのサービスに接続できませんでした(APIキーを確認してください)"
    if "RateLimit" in kind:
        return "AIのサービスの利用上限に達しました。しばらくしてからやり直してください"
    if "Connection" in kind:
        return "AIのサービスに接続できませんでした(接続先の設定を確認してください)"
    return "AIの呼び出しに失敗しました。しばらくしてからやり直してください"


# ---------------------------------------------------------------------
# 見本の注文書にだけ答える偽物
# ---------------------------------------------------------------------
class FakeProvider:
    """fake_answers/*.json の match の文字が PDF に含まれていれば、その答えを返す。"""

    name = "fake"
    model = "fake-answers"

    def __init__(self, answers: list[dict] | None = None):
        self._answers = answers if answers is not None else _load_fake_answers()

    def _find(self, text: str) -> dict | None:
        return next((a for a in self._answers if a["match"] in text), None)

    def classify(self, text: str) -> tuple[Classification, Usage]:
        answer = self._find(text)
        if answer is None:
            return Classification(doc_type="other", reason="見本の注文書ではありません(fake は見本にだけ答えます)"), Usage()
        return Classification.model_validate(answer["classification"]), Usage(len(text) // 2, 20)

    def extract(self, text: str) -> tuple[ExtractedOrder, Usage]:
        answer = self._find(text)
        if answer is None or answer.get("extraction") is None:
            raise ExtractionError("見本の答えがありません")
        return ExtractedOrder.model_validate(answer["extraction"]), Usage(len(text) // 2, 400)


def _load_fake_answers() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FAKE_DIR.glob("*.json"))]


# ---------------------------------------------------------------------
# 設定から選ぶ
# ---------------------------------------------------------------------
def is_available() -> bool:
    return bool(settings.ai_provider)


def provider_label() -> str:
    if settings.ai_provider == "fake":
        return "見本用(fake)"
    if settings.ai_provider == "openai_compatible":
        return f"{settings.ai_model}({settings.ai_base_url or 'OpenAI'})"
    return ""


def get_provider() -> Provider:
    if settings.ai_provider == "fake":
        return FakeProvider()
    if settings.ai_provider == "openai_compatible":
        if not settings.ai_model:
            raise ProviderError("AI_MODEL が設定されていません")
        from langchain_openai import ChatOpenAI

        chat = ChatOpenAI(
            model=settings.ai_model,
            base_url=settings.ai_base_url or None,
            api_key=settings.ai_api_key or "not-needed",  # Ollama は鍵が要らない
            temperature=0,
            timeout=settings.ai_timeout_seconds,
            max_retries=1,
        )
        return LangChainProvider(chat, name="openai_compatible", model=settings.ai_model, method=settings.ai_structured_method)
    raise ProviderError("AI の提供元が設定されていません(AI_PROVIDER)")
