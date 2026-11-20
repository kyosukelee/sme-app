"""ワークフロー(申請・承認)の決まりごと。DB に触れない純粋な関数だけを置く。

- 申請種別の初期セット(経費精算・購買申請・休暇申請・汎用申請)と入力項目の定義
- 入力項目の確認(定義データに沿って、必須・形式・日付の前後を確かめる)
- 承認ルートの組み立て(金額条件、本人の段の省略、同じ人が続く段の省略)
- 状態ごとにできる操作
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

# ---------------------------------------------------------------------
# 申請種別の初期セット
# ---------------------------------------------------------------------
# 入力項目の型: text(1行) / textarea(複数行) / date / number / select
# after: この項目の日付が、指定した項目の日付より前にならないようにする(休暇の期間など)
DEFAULT_TYPES: list[dict] = [
    {
        "code": "expense",
        "name": "経費精算",
        "description": "立て替えた経費の精算。領収書を添付してください。",
        "amount_mode": "required",
        "amount_label": "金額",
        "form_schema": [
            {"key": "used_on", "label": "利用日", "type": "date", "required": True},
            {"key": "payee", "label": "支払先", "type": "text", "required": True, "max_length": 100},
            {"key": "purpose", "label": "用途", "type": "textarea", "required": True, "max_length": 500},
        ],
        "steps": [{"approver_kind": "manager"}, {"approver_kind": "group", "group": "経理担当"}],
    },
    {
        "code": "purchase",
        "name": "購買申請",
        "description": "備品・消耗品・サービスの購入。一定の金額以上は代表の承認が要ります。",
        "amount_mode": "required",
        "amount_label": "見込金額",
        "form_schema": [
            {"key": "item_name", "label": "品名", "type": "text", "required": True, "max_length": 100},
            {"key": "quantity", "label": "数量", "type": "number", "required": True, "min": 1},
            {"key": "vendor", "label": "購入先", "type": "text", "required": False, "max_length": 100},
            {"key": "reason", "label": "理由", "type": "textarea", "required": True, "max_length": 500},
        ],
        "steps": [{"approver_kind": "manager"}, {"approver_kind": "group", "group": "代表", "min_amount": 100000}],
    },
    {
        "code": "leave",
        "name": "休暇申請",
        "description": "有給休暇・特別休暇・欠勤の届け出。",
        "amount_mode": "none",
        "amount_label": "金額",
        "form_schema": [
            {"key": "leave_kind", "label": "種類", "type": "select", "required": True, "options": ["有給休暇", "特別休暇", "欠勤"]},
            {"key": "date_from", "label": "開始日", "type": "date", "required": True},
            {"key": "date_to", "label": "終了日", "type": "date", "required": True, "after": "date_from"},
            {"key": "half_day", "label": "半日", "type": "select", "required": False, "options": ["午前休", "午後休"]},
            {"key": "reason", "label": "理由", "type": "textarea", "required": False, "max_length": 500},
        ],
        "steps": [{"approver_kind": "manager"}],
    },
    {
        "code": "general",
        "name": "汎用申請",
        "description": "ほかの種別に当てはまらない申請・稟議。",
        "amount_mode": "optional",
        "amount_label": "金額",
        "form_schema": [
            {"key": "body", "label": "本文", "type": "textarea", "required": True, "max_length": 2000},
        ],
        "steps": [{"approver_kind": "manager"}],
    },
]

MAX_STEPS = 3


class WorkflowError(ValueError):
    """申請・承認の決まりに反する操作。メッセージはそのまま画面に出す。"""


# ---------------------------------------------------------------------
# 入力項目の確認
# ---------------------------------------------------------------------
def _as_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def clean_form(schema: list[dict], data: dict, *, partial: bool = False) -> dict:
    """定義に沿って入力値を確かめ、保存する形(定義にある項目だけ、空欄は除く)にして返す。

    partial=True(下書きの保存)では、未入力の必須項目を許す。形の誤りは下書きでも受け付けない。
    """
    cleaned: dict = {}
    for f in schema:
        key, label, kind = f["key"], f["label"], f["type"]
        raw = data.get(key)
        value = raw.strip() if isinstance(raw, str) else raw
        if value in (None, ""):
            if f.get("required") and not partial:
                raise WorkflowError(f"「{label}」を入力してください")
            continue
        if kind in ("text", "textarea"):
            value = str(value)
            if len(value) > f.get("max_length", 1000):
                raise WorkflowError(f"「{label}」は{f.get('max_length', 1000)}文字以内で入力してください")
        elif kind == "date":
            if _as_date(value) is None:
                raise WorkflowError(f"「{label}」の日付の形が正しくありません")
            value = str(value)
        elif kind == "number":
            try:
                number = Decimal(str(value))
            except InvalidOperation:
                raise WorkflowError(f"「{label}」は数字で入力してください") from None
            if not number.is_finite() or number < Decimal(str(f.get("min", 0))):
                raise WorkflowError(f"「{label}」は{f.get('min', 0)}以上で入力してください")
            value = format(number.normalize(), "f")
        elif kind == "select":
            if value not in f.get("options", []):
                raise WorkflowError(f"「{label}」は選択肢から選んでください")
        cleaned[key] = value

    for f in schema:
        before = f.get("after")
        if before and f["key"] in cleaned and before in cleaned:
            if _as_date(cleaned[f["key"]]) < _as_date(cleaned[before]):
                other = next(x["label"] for x in schema if x["key"] == before)
                raise WorkflowError(f"「{f['label']}」は「{other}」以降の日付にしてください")
    return cleaned


def check_amount(mode: str, amount: Decimal | None, label: str = "金額", *, partial: bool = False) -> Decimal | None:
    if mode == "none":
        return None
    if amount is None:
        if mode == "required" and not partial:
            raise WorkflowError(f"「{label}」を入力してください")
        return None
    if amount < 0 or amount != amount.to_integral_value():
        raise WorkflowError(f"「{label}」は0以上の整数(円)で入力してください")
    return amount


# ---------------------------------------------------------------------
# 承認ルートの組み立て
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class StepDef:
    """申請種別に設定した承認の段。"""

    approver_kind: str  # manager / group / user
    group_id: UUID | None = None
    group_name: str | None = None
    user_id: UUID | None = None
    min_amount: Decimal | None = None


@dataclass(frozen=True)
class Person:
    user_id: UUID
    name: str
    is_active: bool = True


@dataclass
class RouteStep:
    """申請に写す承認の段。status は waiting(これから)か skipped(省略)。"""

    step_no: int
    approver_kind: str
    label: str
    candidates: list[UUID] = field(default_factory=list)
    status: str = "waiting"
    skip_reason: str | None = None


SKIP_SELF = "本人のため省略"
SKIP_NO_MANAGER = "上長がいないため省略"
SKIP_SAME = "前の段と同じ承認者のため省略"


def build_route(
    steps: list[StepDef],
    *,
    applicant: UUID,
    amount: Decimal | None,
    manager: Person | None,
    group_members: dict[UUID, list[Person]],
    people: dict[UUID, Person],
) -> list[RouteStep]:
    """申請者と金額から、この申請の承認ルートを作る。

    - min_amount のある段は、金額がその額以上のときだけ使う(金額がなければ使わない)
    - 承認者から申請者本人を除く。誰も残らなければ「本人のため省略」
    - 上長がいない(代表など)ときは「上長がいないため省略」
    - 1人だけの段で、その人が前の段の唯一の承認者なら「前の段と同じ承認者のため省略」
    - 承認できる人がいない段(担当役割に社員がいない、使用中止の社員など)があれば申請できない
    """
    route: list[RouteStep] = []
    approved_alone: set[UUID] = set()  # 前の段で1人で承認する人
    for s in steps:
        if s.min_amount is not None and (amount is None or amount < s.min_amount):
            continue
        no = len(route) + 1
        if s.approver_kind == "manager":
            label = "上長"
            if manager is None:
                route.append(RouteStep(no, "manager", label, [], "skipped", SKIP_NO_MANAGER))
                continue
            if not manager.is_active:
                raise WorkflowError(f"上長の{manager.name}さんが使用中止のため申請できません。管理者に上長の設定を依頼してください")
            candidates = [manager.user_id]
        elif s.approver_kind == "group":
            label = s.group_name or "担当役割"
            members = [p for p in group_members.get(s.group_id, []) if p.is_active]
            if not members:
                raise WorkflowError(f"担当役割「{label}」に社員がいないため申請できません。管理者に設定を依頼してください")
            candidates = [p.user_id for p in members]
        elif s.approver_kind == "user":
            person = people.get(s.user_id)
            if person is None or not person.is_active:
                name = f"{person.name}さん" if person else "指定された承認者"
                raise WorkflowError(f"{name}が使用中止のため申請できません。管理者に承認ルートの設定を依頼してください")
            label = person.name
            candidates = [person.user_id]
        else:
            raise WorkflowError(f"承認者の指定方法が正しくありません: {s.approver_kind}")

        others = [u for u in candidates if u != applicant]
        if not others:
            route.append(RouteStep(no, s.approver_kind, label, candidates, "skipped", SKIP_SELF))
            continue
        if len(others) == 1 and others[0] in approved_alone:
            route.append(RouteStep(no, s.approver_kind, label, others, "skipped", SKIP_SAME))
            continue
        route.append(RouteStep(no, s.approver_kind, label, others))
        if len(others) == 1:
            approved_alone.add(others[0])
    return route


def check_route_definition(steps: list[StepDef]) -> None:
    """申請種別に設定する段の確認(画面の設定を保存するとき)。"""
    if len(steps) > MAX_STEPS:
        raise WorkflowError(f"承認の段は{MAX_STEPS}段までです")
    for i, s in enumerate(steps, start=1):
        if s.approver_kind == "group" and s.group_id is None:
            raise WorkflowError(f"{i}段目の担当役割を選んでください")
        if s.approver_kind == "user" and s.user_id is None:
            raise WorkflowError(f"{i}段目の承認者を選んでください")
        if s.min_amount is not None and s.min_amount <= 0:
            raise WorkflowError(f"{i}段目の金額の条件は1円以上にしてください")


# ---------------------------------------------------------------------
# 状態ごとにできる操作
# ---------------------------------------------------------------------
STATUS_LABELS = {
    "draft": "下書き",
    "pending": "承認待ち",
    "returned": "差戻し",
    "approved": "承認済",
    "rejected": "却下",
    "withdrawn": "取下げ",
}

_ALLOWED = {
    "edit": {"draft", "returned"},
    "submit": {"draft", "returned"},
    "approve": {"pending"},
    "return": {"pending"},
    "reject": {"pending"},
    "withdraw": {"pending", "returned"},
    "delete": {"draft"},
}

_ACTION_LABELS = {
    "edit": "直す",
    "submit": "申請する",
    "approve": "承認する",
    "return": "差し戻す",
    "reject": "却下する",
    "withdraw": "取り下げる",
    "delete": "削除する",
}


def can(status: str, action: str) -> bool:
    return status in _ALLOWED[action]


def check_action(status: str, action: str) -> None:
    if not can(status, action):
        raise WorkflowError(f"{STATUS_LABELS.get(status, status)}の申請は{_ACTION_LABELS[action]}ことができません")


def require_comment(action: str, comment: str | None) -> str | None:
    """差戻しと却下は理由のコメントを必須にする。"""
    text = (comment or "").strip() or None
    if action in ("return", "reject") and text is None:
        raise WorkflowError("差し戻し・却下のときは理由を入力してください")
    if text is not None and len(text) > 1000:
        raise WorkflowError("コメントは1000文字以内で入力してください")
    return text
