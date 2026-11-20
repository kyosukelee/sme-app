"""ワークフローの決まりごと(承認ルートの組み立て・入力項目の確認・状態ごとの操作)のテスト。DB なしで動く。"""

from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import workflow as wf

YAMADA = UUID(int=1)  # 代表
SATO = UUID(int=2)  # 営業部長(上長: 山田)
SUZUKI = UUID(int=3)  # 営業部(上長: 佐藤)
TANAKA = UUID(int=4)  # 経理
REP, ACC = UUID(int=100), UUID(int=101)

PEOPLE = {
    YAMADA: wf.Person(YAMADA, "山田 太郎"),
    SATO: wf.Person(SATO, "佐藤 花子"),
    SUZUKI: wf.Person(SUZUKI, "鈴木 一郎"),
    TANAKA: wf.Person(TANAKA, "田中 美咲"),
}
GROUPS = {REP: [PEOPLE[YAMADA]], ACC: [PEOPLE[TANAKA]]}

PURCHASE = [wf.StepDef("manager"), wf.StepDef("group", group_id=REP, group_name="代表", min_amount=Decimal(100000))]
EXPENSE = [wf.StepDef("manager"), wf.StepDef("group", group_id=ACC, group_name="経理担当")]


def route(steps, applicant, manager, amount=None, groups=GROUPS):
    r = wf.build_route(steps, applicant=applicant, amount=amount, manager=PEOPLE.get(manager) if manager else None,
                       group_members=groups, people=PEOPLE)
    return [(s.label, s.status, s.candidates, s.skip_reason) for s in r]


def test_金額の条件で段が増える():
    assert route(PURCHASE, SUZUKI, SATO, Decimal(99999)) == [("上長", "waiting", [SATO], None)]
    assert route(PURCHASE, SUZUKI, SATO, Decimal(100000)) == [
        ("上長", "waiting", [SATO], None),
        ("代表", "waiting", [YAMADA], None),
    ]
    # 金額がなければ条件つきの段は使わない
    assert len(route(PURCHASE, SUZUKI, SATO, None)) == 1


def test_本人の段は省略する():
    # 経理担当の田中さん自身の経費精算: 上長(山田)→経理担当(本人のため省略)
    assert route(EXPENSE, TANAKA, YAMADA, Decimal(1200)) == [
        ("上長", "waiting", [YAMADA], None),
        ("経理担当", "skipped", [TANAKA], wf.SKIP_SELF),
    ]


def test_担当役割が複数人なら本人だけを外す():
    groups = {**GROUPS, ACC: [PEOPLE[TANAKA], PEOPLE[SATO]]}
    assert route(EXPENSE, TANAKA, YAMADA, groups=groups)[1] == ("経理担当", "waiting", [SATO], None)


def test_代表の申請はすべて省略される():
    r = route(PURCHASE, YAMADA, None, Decimal(500000))
    assert r == [
        ("上長", "skipped", [], wf.SKIP_NO_MANAGER),
        ("代表", "skipped", [YAMADA], wf.SKIP_SELF),
    ]


def test_同じ人が続く段は省略する():
    # 佐藤さんの購買申請: 上長(山田)→代表(山田)は、代表の段を省略する
    assert route(PURCHASE, SATO, YAMADA, Decimal(200000)) == [
        ("上長", "waiting", [YAMADA], None),
        ("代表", "skipped", [YAMADA], wf.SKIP_SAME),
    ]


def test_承認できる人がいないと申請できない():
    with pytest.raises(wf.WorkflowError, match="経理担当"):
        route(EXPENSE, SUZUKI, SATO, groups={**GROUPS, ACC: []})
    with pytest.raises(wf.WorkflowError, match="使用中止"):
        wf.build_route([wf.StepDef("manager")], applicant=SUZUKI, amount=None,
                       manager=wf.Person(SATO, "佐藤 花子", is_active=False), group_members={}, people=PEOPLE)
    with pytest.raises(wf.WorkflowError, match="使用中止"):
        wf.build_route([wf.StepDef("user", user_id=SATO)], applicant=SUZUKI, amount=None, manager=None,
                       group_members={}, people={SATO: wf.Person(SATO, "佐藤 花子", is_active=False)})


def test_特定の人を承認者にする():
    r = wf.build_route([wf.StepDef("manager"), wf.StepDef("user", user_id=TANAKA)], applicant=SUZUKI, amount=None,
                       manager=PEOPLE[SATO], group_members={}, people=PEOPLE)
    assert [(s.step_no, s.label, s.candidates) for s in r] == [(1, "上長", [SATO]), (2, "田中 美咲", [TANAKA])]


def test_段の設定の確認():
    with pytest.raises(wf.WorkflowError, match="3段まで"):
        wf.check_route_definition([wf.StepDef("manager")] * 4)
    with pytest.raises(wf.WorkflowError, match="2段目の担当役割"):
        wf.check_route_definition([wf.StepDef("manager"), wf.StepDef("group")])


LEAVE = next(t for t in wf.DEFAULT_TYPES if t["code"] == "leave")["form_schema"]
PURCHASE_FORM = next(t for t in wf.DEFAULT_TYPES if t["code"] == "purchase")["form_schema"]


def test_入力項目の確認():
    data = wf.clean_form(LEAVE, {"leave_kind": "有給休暇", "date_from": "2026-12-01", "date_to": "2026-12-02",
                                 "reason": " 私用 ", "unknown": "捨てる"})
    assert data == {"leave_kind": "有給休暇", "date_from": "2026-12-01", "date_to": "2026-12-02", "reason": "私用"}
    with pytest.raises(wf.WorkflowError, match="「終了日」は「開始日」以降"):
        wf.clean_form(LEAVE, {"leave_kind": "有給休暇", "date_from": "2026-12-02", "date_to": "2026-12-01"})
    with pytest.raises(wf.WorkflowError, match="選択肢"):
        wf.clean_form(LEAVE, {"leave_kind": "長期休暇", "date_from": "2026-12-01", "date_to": "2026-12-01"})
    with pytest.raises(wf.WorkflowError, match="「種類」を入力"):
        wf.clean_form(LEAVE, {"date_from": "2026-12-01", "date_to": "2026-12-01"})
    # 下書きは必須の未入力を許すが、形の誤りは許さない
    assert wf.clean_form(LEAVE, {"date_from": "2026-12-01"}, partial=True) == {"date_from": "2026-12-01"}
    with pytest.raises(wf.WorkflowError, match="日付の形"):
        wf.clean_form(LEAVE, {"date_from": "12月1日"}, partial=True)
    assert wf.clean_form(PURCHASE_FORM, {"item_name": "PC", "quantity": "2.0", "reason": "更新"})["quantity"] == "2"
    with pytest.raises(wf.WorkflowError, match="1以上"):
        wf.clean_form(PURCHASE_FORM, {"item_name": "PC", "quantity": "0", "reason": "更新"})


def test_金額の確認():
    assert wf.check_amount("none", Decimal(100)) is None
    assert wf.check_amount("optional", None) is None
    with pytest.raises(wf.WorkflowError, match="見込金額"):
        wf.check_amount("required", None, "見込金額")
    assert wf.check_amount("required", None, partial=True) is None
    with pytest.raises(wf.WorkflowError):
        wf.check_amount("required", Decimal(-1))


@pytest.mark.parametrize(
    ("status", "action", "ok"),
    [
        ("draft", "submit", True), ("returned", "submit", True), ("pending", "submit", False),
        ("pending", "approve", True), ("returned", "approve", False),
        ("pending", "withdraw", True), ("returned", "withdraw", True), ("approved", "withdraw", False),
        ("draft", "delete", True), ("returned", "delete", False),
        ("returned", "edit", True), ("pending", "edit", False), ("rejected", "edit", False),
    ],
)
def test_状態ごとにできる操作(status, action, ok):
    assert wf.can(status, action) is ok


def test_差戻しと却下は理由が必須():
    with pytest.raises(wf.WorkflowError, match="理由"):
        wf.require_comment("return", "  ")
    with pytest.raises(wf.WorkflowError, match="理由"):
        wf.require_comment("reject", None)
    assert wf.require_comment("approve", "") is None
    assert wf.require_comment("approve", " OK ") == "OK"
