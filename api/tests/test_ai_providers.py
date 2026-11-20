"""LangChain で OpenAI 互換の API を呼ぶ部分(手元に偽の API サーバーを立てて確かめる。外部には接続しない)。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from langchain_openai import ChatOpenAI

from app.ai_import.graph import read_order
from app.ai_import.providers import ExtractionError, LangChainProvider, ProviderError

ORDER = {
    "issuer_name": "(株)瀬戸内物産",
    "customer_order_ref": "SN-9",
    "order_date": "2026-11-20",
    "lines": [{"description": "A4コピー用紙", "quantity": "20", "unit": "箱", "unit_price": "600", "amount": "12,000"}],
    "total": "13,200",
}


class FakeOpenAI(BaseHTTPRequestHandler):
    """chat/completions に、呼び出された道具(構造化出力)の引数を返す。"""

    replies: list = []      # 順に返す。(状態コード, 道具の引数 or None)
    requests: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.requests.append(body)
        code, arguments = FakeOpenAI.replies.pop(0)
        if code != 200:
            payload = {"error": {"message": "invalid api key", "type": "invalid_request_error"}}
        else:
            tool = body["tools"][0]["function"]["name"]
            payload = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [{
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "call_1", "type": "function",
                                        "function": {"name": tool, "arguments": arguments}}],
                    },
                }],
                "usage": {"prompt_tokens": 321, "completion_tokens": 45, "total_tokens": 366},
            }
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()


@pytest.fixture
def provider(server):
    FakeOpenAI.replies.clear()
    FakeOpenAI.requests.clear()
    chat = ChatOpenAI(model="test-model", base_url=server, api_key="test", temperature=0, timeout=5, max_retries=0)
    return LangChainProvider(chat, name="openai_compatible", model="test-model")


def test_種類の判定と抽出を構造化出力で受け取る(provider):
    FakeOpenAI.replies[:] = [
        (200, json.dumps({"doc_type": "order", "reason": "表題が注文書"})),
        (200, json.dumps(ORDER, ensure_ascii=False)),
    ]
    result = read_order(provider, "注文書 SN-9 ...")
    assert result.error is None
    assert result.order.customer_order_ref == "SN-9"
    assert result.order.lines[0].unit_price == "600"
    assert result.input_tokens == 642 and result.output_tokens == 90

    first, second = FakeOpenAI.requests
    assert first["tools"][0]["function"]["name"] == "Classification"
    assert second["tools"][0]["function"]["name"] == "ExtractedOrder"
    assert first["messages"][0]["role"] == "system" and "書類の種類" in first["messages"][0]["content"]
    assert second["temperature"] == 0


def test_形が合わなければ1回だけやり直す(provider):
    FakeOpenAI.replies[:] = [
        (200, json.dumps({"doc_type": "order"})),
        (200, json.dumps({"lines": "表が読めません"}, ensure_ascii=False)),  # lines が一覧でない
        (200, json.dumps(ORDER, ensure_ascii=False)),
    ]
    result = read_order(provider, "注文書")
    assert result.error is None and result.order.issuer_name == "(株)瀬戸内物産"
    assert len(FakeOpenAI.requests) == 3


def test_2回とも形が合わなければ失敗にする(provider):
    FakeOpenAI.replies[:] = [(200, json.dumps({"lines": 1})), (200, json.dumps({"lines": 2}))]
    with pytest.raises(ExtractionError):
        provider.extract("注文書")
    FakeOpenAI.replies[:] = [(200, json.dumps({"lines": 1})), (200, json.dumps({"lines": 2}))]  # 種類の判定は飛ばす
    result = read_order(provider, "注文書", skip_classify=True)
    assert result.order is None and "決められた形" in result.error


def test_認証の失敗は画面に出せる文にする(provider):
    FakeOpenAI.replies[:] = [(401, None)]
    with pytest.raises(ProviderError, match="APIキー"):
        provider.classify("注文書")
