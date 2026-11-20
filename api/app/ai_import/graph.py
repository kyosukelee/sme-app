"""注文書の読み取りの流れ(LangGraph)。

  種類の判定 ─(注文書)→ 項目の抽出 ─(形が合わない)→ もう一度だけ抽出
      └─(それ以外)→ 終わり(人に知らせる)

ここで扱うのは AI を使う部分だけ。取引先・商品の照合と検証は DB を読む普通のコードで行う
(runner.py → review.py)。AI を待つあいだは DB のトランザクションを開けたままにしない。
人の確認は数日かかることもあるので、LangGraph の中断機能ではなく DB の状態(要確認)で止める。
"""

from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.ai_import.providers import ExtractionError, Provider, Usage
from app.ai_import.schema import Classification, ExtractedOrder

MAX_EXTRACT_ATTEMPTS = 2


class State(TypedDict, total=False):
    text: str
    skip_classify: bool
    classification: Classification
    order: ExtractedOrder
    attempts: int
    usage: list[Usage]
    error: str


@dataclass
class ReadResult:
    classification: Classification | None
    order: ExtractedOrder | None
    usage: list[Usage] = field(default_factory=list)
    error: str | None = None

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.usage)

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.usage)


def build_graph(provider: Provider):
    def classify(state: State) -> State:
        if state.get("skip_classify"):
            # 人が「注文書として読み直す」を選んだとき
            return {"classification": Classification(doc_type="order", reason="利用者が注文書として指定")}
        result, usage = provider.classify(state["text"])
        return {"classification": result, "usage": [*state.get("usage", []), usage]}

    def extract(state: State) -> State:
        attempts = state.get("attempts", 0) + 1
        try:
            order, usage = provider.extract(state["text"])
        except ExtractionError as e:
            return {"attempts": attempts, "error": f"AIの答えが決められた形になりませんでした({str(e)[:200]})"}
        return {"attempts": attempts, "order": order, "error": "", "usage": [*state.get("usage", []), usage]}

    def after_classify(state: State) -> str:
        return "extract" if state["classification"].doc_type == "order" else END

    def after_extract(state: State) -> str:
        if state.get("order") is None and state.get("attempts", 0) < MAX_EXTRACT_ATTEMPTS:
            return "extract"
        return END

    graph = StateGraph(State)
    graph.add_node("classify", classify)
    graph.add_node("extract", extract)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", after_classify, ["extract", END])
    graph.add_conditional_edges("extract", after_extract, ["extract", END])
    return graph.compile()


def read_order(provider: Provider, text: str, *, skip_classify: bool = False) -> ReadResult:
    final = build_graph(provider).invoke({"text": text, "skip_classify": skip_classify, "usage": []})
    classification = final.get("classification")
    order = final.get("order")
    error = final.get("error") or None
    if classification is not None and classification.doc_type != "order":
        kind = "見積依頼書" if classification.doc_type == "quote_request" else "注文書ではない書類"
        error = (
            f"{kind}と判定されました({classification.reason})。"
            "注文書の場合は「注文書として読み直す」を押してください"
        )
    return ReadResult(classification=classification, order=order, usage=final.get("usage", []), error=error)
