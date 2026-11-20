import { useCallback, useEffect, useState } from "react";
import { api, type ApproverGroup, type ApproverKind, type Member, type RequestType, type RequestTypeInput } from "../../api";
import { routeSummary } from "./RequestEditor";

type StepForm = RequestTypeInput["steps"][number];

const KIND_LABELS: Record<ApproverKind, string> = { manager: "申請者の上長", group: "担当役割", user: "特定の社員" };

/**
 * 申請種別と承認ルートの設定。種別ごとに承認の段(最大3段)を、上長・担当役割・特定の社員から選ぶ。
 * 金額の条件をつけた段は、申請の金額がその額以上のときだけ使う。変更は管理者だけ
 */
export default function RequestTypes({ tenantId }: { tenantId: string }) {
  const [types, setTypes] = useState<RequestType[] | null>(null);
  const [groups, setGroups] = useState<ApproverGroup[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [me, m, g] = await Promise.all([api.myProfile(tenantId), api.members(tenantId), api.approverGroups(tenantId)]);
    setIsAdmin(me.role === "admin");
    setMembers(m);
    setGroups(g);
    setTypes(await api.requestTypes(tenantId, me.role === "admin"));
  }, [tenantId]);

  useEffect(() => {
    load().catch((e: Error) => setError(e.message));
  }, [load]);

  return (
    <>
      <div className="page-head">
        <h1>申請種別・承認ルート</h1>
      </div>
      <p className="lead">
        申請の種類ごとに、誰が・どの順番で承認するかを決めます。申請者本人が承認者になる段は自動で省略されます。設定を変えても、承認待ちの申請のルートは変わりません。
      </p>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {!types ? (
        <p className="placeholder">読み込み中…</p>
      ) : (
        <div className="stack">
          {types.map((t) =>
            editing === t.id ? (
              <TypeForm
                key={t.id}
                tenantId={tenantId}
                type={t}
                groups={groups}
                members={members}
                onDone={() => {
                  setEditing(null);
                  load().catch((e: Error) => setError(e.message));
                }}
                onCancel={() => setEditing(null)}
              />
            ) : (
              <section key={t.id} className={`panel type-row${t.is_active ? "" : " is-inactive"}`}>
                <div className="panel-head">
                  <h2>
                    {t.name}
                    {!t.is_active && <span className="badge inline is-lost">使っていない</span>}
                  </h2>
                  {isAdmin && editing === null && (
                    <button type="button" className="secondary" onClick={() => setEditing(t.id)}>
                      編集
                    </button>
                  )}
                </div>
                {t.description && <p className="hint">{t.description}</p>}
                <ol className="step-summary">
                  {t.steps.map((s) => (
                    <li key={s.step_no}>
                      <span className="step-no">{s.step_no}</span>
                      <span>
                        <strong>{s.label}</strong>
                        <small>{s.approver_kind === "manager" ? "申請者の上長" : s.approver_kind === "group" ? "担当役割のどなたか1人" : "特定の社員"}</small>
                      </span>
                      {s.min_amount && <span className="condition">{Number(s.min_amount).toLocaleString("ja-JP")}円以上のとき</span>}
                    </li>
                  ))}
                </ol>
                <p className="fields-line">
                  入力項目: {t.form_schema.map((f) => f.label).join("・")}
                  {t.amount_mode !== "none" && `・${t.amount_label}${t.amount_mode === "optional" ? "(任意)" : ""}`}
                </p>
              </section>
            ),
          )}
          {!isAdmin && <p className="hint">申請種別と承認ルートを変えられるのは管理者だけです。</p>}
        </div>
      )}
    </>
  );
}

function TypeForm({
  tenantId,
  type,
  groups,
  members,
  onDone,
  onCancel,
}: {
  tenantId: string;
  type: RequestType;
  groups: ApproverGroup[];
  members: Member[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(type.name);
  const [description, setDescription] = useState(type.description ?? "");
  const [active, setActive] = useState(type.is_active);
  const [steps, setSteps] = useState<StepForm[]>(
    type.steps.map((s) => ({
      approver_kind: s.approver_kind,
      approver_group_id: s.approver_group_id,
      approver_user_id: s.approver_user_id,
      min_amount: s.min_amount,
    })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function change(i: number, patch: Partial<StepForm>) {
    setSteps((list) => list.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      await api.updateRequestType(tenantId, type.id, {
        name: name.trim(),
        description: description.trim() || null,
        is_active: active,
        steps: steps.map((s) => ({
          approver_kind: s.approver_kind,
          approver_group_id: s.approver_kind === "group" ? s.approver_group_id : null,
          approver_user_id: s.approver_kind === "user" ? s.approver_user_id : null,
          min_amount: s.min_amount ? s.min_amount.replace(/[,，円\s]/g, "") || null : null,
        })),
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <section className="panel type-form">
      <h2>{type.name}を編集</h2>
      <div className="field-row">
        <label>
          種別の名前
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={30} />
        </label>
        <label>
          説明(任意)
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={200} />
        </label>
      </div>

      <fieldset className="step-editor">
        <legend>承認の段(上から順に承認します。3段まで)</legend>
        {steps.map((s, i) => (
          <div key={i} className="step-edit-row">
            <span className="step-no">{i + 1}</span>
            <label>
              承認する人
              <select value={s.approver_kind} onChange={(e) => change(i, { approver_kind: e.target.value as ApproverKind })}>
                {(Object.keys(KIND_LABELS) as ApproverKind[]).map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABELS[k]}
                  </option>
                ))}
              </select>
            </label>
            {s.approver_kind === "group" && (
              <label>
                担当役割
                <select value={s.approver_group_id ?? ""} onChange={(e) => change(i, { approver_group_id: e.target.value || null })}>
                  <option value="">選んでください</option>
                  {groups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}({g.members.filter((m) => m.is_active).length}人)
                    </option>
                  ))}
                </select>
              </label>
            )}
            {s.approver_kind === "user" && (
              <label>
                社員
                <select value={s.approver_user_id ?? ""} onChange={(e) => change(i, { approver_user_id: e.target.value || null })}>
                  <option value="">選んでください</option>
                  {members.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.display_name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {s.approver_kind === "manager" && <span className="hint step-hint">社員の画面で設定した上長が承認します</span>}
            {type.amount_mode !== "none" && (
              <label className="narrow">
                金額の条件(任意)
                <input
                  inputMode="numeric"
                  className="num"
                  value={s.min_amount ?? ""}
                  onChange={(e) => change(i, { min_amount: e.target.value || null })}
                  placeholder="例: 100000"
                />
                <span className="hint">この金額以上のときだけ</span>
              </label>
            )}
            <button
              type="button"
              className="link-button"
              onClick={() => setSteps((list) => list.filter((_, j) => j !== i))}
              disabled={steps.length <= 1}
            >
              この段を外す
            </button>
          </div>
        ))}
        {steps.length < 3 && (
          <button
            type="button"
            className="secondary add-step"
            onClick={() => setSteps((list) => [...list, { approver_kind: "group", approver_group_id: null, approver_user_id: null, min_amount: null }])}
          >
            段を追加
          </button>
        )}
      </fieldset>

      <label className="check">
        <input type="checkbox" checked={!active} onChange={(e) => setActive(!e.target.checked)} />
        この種別を使わない
        <span className="hint">申請の作成画面に出なくなります。これまでの申請はそのまま残ります。</span>
      </label>

      <p className="hint">
        変更前: {routeSummary(type)}
      </p>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="actions inline">
        <button type="button" onClick={save} disabled={busy || !name.trim()}>
          {busy ? "保存しています…" : "保存"}
        </button>
        <button type="button" className="secondary" onClick={onCancel} disabled={busy}>
          やめる
        </button>
      </div>
    </section>
  );
}
