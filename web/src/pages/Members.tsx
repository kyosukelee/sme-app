import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type ApproverGroup, type Member, type MemberInput } from "../api";

const EMPTY: MemberInput = { display_name: "", email: null, department: null, manager_user_id: null, role: "member", is_active: true };

/**
 * 社員と担当役割。ワークフローの承認ルート(申請者の上長、経理担当・代表などの担当役割)に使う。
 * 変更は管理者だけ
 */
export default function Members({ tenantId }: { tenantId: string }) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [groups, setGroups] = useState<ApproverGroup[]>([]);
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [m, g] = await Promise.all([api.members(tenantId, true), api.approverGroups(tenantId)]);
    setMembers(m);
    setGroups(g);
  }, [tenantId]);

  useEffect(() => {
    setMembers(null);
    setEditing(null);
    load().catch((e: Error) => setError(e.message));
  }, [load]);

  const isAdmin = members?.some((m) => m.is_me && m.role === "admin") ?? false;
  const inactiveCount = members?.filter((m) => !m.is_active).length ?? 0;
  const shown = members?.filter((m) => showInactive || m.is_active) ?? null;
  const active = members?.filter((m) => m.is_active) ?? [];

  function saved() {
    setEditing(null);
    load().catch((e: Error) => setError(e.message));
  }

  return (
    <>
      <div className="page-head">
        <h1>社員・担当役割</h1>
        {members && <p className="count">{members.length - inactiveCount}人</p>}
      </div>
      <p className="lead">
        ワークフローの承認ルートは「申請者の上長」と「担当役割(経理担当・代表など)」から決まります。社員ごとに上長を、担当役割ごとに担当する社員を設定します。
      </p>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      <div className={isAdmin ? "columns" : undefined}>
        <div className="list-col">
          {shown === null ? (
            <p className="placeholder">読み込み中…</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">氏名</th>
                    <th scope="col">部署</th>
                    <th scope="col">上長</th>
                    <th scope="col">権限</th>
                    <th scope="col">担当役割</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((m) => (
                    <tr
                      key={m.user_id}
                      className={[m.user_id === editing?.user_id && "is-selected", !m.is_active && "is-inactive"].filter(Boolean).join(" ") || undefined}
                    >
                      <td>
                        {isAdmin ? (
                          <button type="button" className="link-button" onClick={() => setEditing(m)} aria-label={`${m.display_name}を編集`}>
                            {m.display_name}
                          </button>
                        ) : (
                          m.display_name
                        )}
                        {m.is_me && <span className="badge inline is-issued">自分</span>}
                        {!m.is_active && <span className="badge inline is-lost">使用中止</span>}
                      </td>
                      <td>{m.department ?? "―"}</td>
                      <td>{m.manager_name ?? "―"}</td>
                      <td>{m.role === "admin" ? "管理者" : "一般"}</td>
                      <td>{m.groups.map((g) => g.name).join("、") || "―"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {inactiveCount > 0 && (
            <label className="check inline-check">
              <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
              使用中止の社員も表示する({inactiveCount}人)
            </label>
          )}
          {!isAdmin && members && <p className="hint">社員と担当役割を変えられるのは管理者だけです。</p>}
        </div>

        {isAdmin && members && (
          <MemberForm
            key={`${tenantId}:${editing?.user_id ?? "new"}`}
            tenantId={tenantId}
            member={editing}
            candidates={active.filter((m) => m.user_id !== editing?.user_id)}
            onSaved={saved}
            onCancel={() => setEditing(null)}
          />
        )}
      </div>

      <GroupsPanel tenantId={tenantId} groups={groups} members={active} canEdit={isAdmin} onChanged={saved} />
    </>
  );
}

function MemberForm({
  tenantId,
  member,
  candidates,
  onSaved,
  onCancel,
}: {
  tenantId: string;
  member: Member | null;
  candidates: Member[];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<MemberInput>(() =>
    member
      ? { display_name: member.display_name, email: member.email, department: member.department, manager_user_id: member.manager_user_id, role: member.role, is_active: member.is_active }
      : EMPTY,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  const text = (key: "display_name" | "email" | "department") => ({
    value: form[key] ?? "",
    onChange: (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value })),
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    const input = { ...form, email: form.email?.trim() || null, department: form.department?.trim() || null };
    try {
      const saved = member ? await api.updateMember(tenantId, member.user_id, input) : await api.createMember(tenantId, input);
      if (!member) setMessage({ kind: "done", text: `${saved.display_name}さんを追加しました` });
      setForm(member ? input : EMPTY);
      onSaved();
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>{member ? `${member.display_name}さんを編集` : "社員を追加"}</h2>
      <label>
        氏名
        <input required {...text("display_name")} placeholder="例: 山田 太郎" />
      </label>
      <label>
        部署(任意)
        <input {...text("department")} placeholder="例: 営業部" />
      </label>
      <label>
        上長
        <select
          value={form.manager_user_id ?? ""}
          onChange={(e) => setForm((f) => ({ ...f, manager_user_id: e.target.value || null }))}
        >
          <option value="">なし(代表など)</option>
          {candidates.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.display_name}
              {m.department ? `(${m.department})` : ""}
            </option>
          ))}
        </select>
        <span className="hint">申請の1段目の承認者になります。</span>
      </label>
      <label>
        メールアドレス(任意)
        <input type="email" {...text("email")} />
        <span className="hint">ログイン機能を入れたときの招待に使います。</span>
      </label>
      <fieldset className="choices">
        <legend>権限</legend>
        <label>
          <input type="radio" name="role" checked={form.role === "member"} onChange={() => setForm((f) => ({ ...f, role: "member" }))} />
          一般
        </label>
        <label>
          <input type="radio" name="role" checked={form.role === "admin"} onChange={() => setForm((f) => ({ ...f, role: "admin" }))} />
          管理者(自社情報・社員・仕訳の設定などを変えられる)
        </label>
      </fieldset>
      {member && (
        <label className="check">
          <input type="checkbox" checked={!form.is_active} onChange={(e) => setForm((f) => ({ ...f, is_active: !e.target.checked }))} />
          使用中止にする
          <span className="hint">退職した社員などに使います。この会社を使えなくなり、承認者の候補にも出なくなります。</span>
        </label>
      )}
      <div className="actions">
        <button type="submit" disabled={busy}>
          {busy ? "保存しています…" : member ? "変更を保存" : "社員を追加"}
        </button>
        {member && (
          <button type="button" className="secondary" onClick={onCancel} disabled={busy}>
            やめる
          </button>
        )}
      </div>
      {message && (
        <p className={message.kind === "done" ? "notice" : "alert"} role="status">
          {message.text}
        </p>
      )}
    </form>
  );
}

function GroupsPanel({
  tenantId,
  groups,
  members,
  canEdit,
  onChanged,
}: {
  tenantId: string;
  groups: ApproverGroup[];
  members: Member[];
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<ApproverGroup | "new" | null>(null);

  return (
    <section className="panel groups-panel">
      <div className="panel-head">
        <h2>担当役割</h2>
        {canEdit && editing === null && (
          <button type="button" className="secondary" onClick={() => setEditing("new")}>
            担当役割を追加
          </button>
        )}
      </div>
      <p className="hint">承認の段に担当役割を指定すると、その役割の社員のうち誰か1人が承認すればよくなります。</p>
      {groups.length === 0 ? (
        <p className="placeholder">担当役割はまだありません。</p>
      ) : (
        <ul className="group-list">
          {groups.map((g) =>
            editing !== "new" && editing?.id === g.id ? (
              <li key={g.id}>
                <GroupForm tenantId={tenantId} group={g} members={members} onDone={() => { setEditing(null); onChanged(); }} onCancel={() => setEditing(null)} />
              </li>
            ) : (
              <li key={g.id} className="group-row">
                <strong>{g.name}</strong>
                <span className="group-members">
                  {g.members.length ? g.members.map((m) => m.display_name + (m.is_active ? "" : "(使用中止)")).join("、") : "担当者がいません"}
                </span>
                {canEdit && editing === null && (
                  <button type="button" className="link-button" onClick={() => setEditing(g)}>
                    編集
                  </button>
                )}
              </li>
            ),
          )}
        </ul>
      )}
      {editing === "new" && (
        <GroupForm tenantId={tenantId} group={null} members={members} onDone={() => { setEditing(null); onChanged(); }} onCancel={() => setEditing(null)} />
      )}
    </section>
  );
}

function GroupForm({
  tenantId,
  group,
  members,
  onDone,
  onCancel,
}: {
  tenantId: string;
  group: ApproverGroup | null;
  members: Member[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [userIds, setUserIds] = useState<string[]>(group?.members.map((m) => m.user_id) ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setBusy(true);
    setError("");
    try {
      const body = { name: name.trim(), user_ids: userIds };
      if (group) await api.updateApproverGroup(tenantId, group.id, body);
      else await api.createApproverGroup(tenantId, body);
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function remove() {
    if (!group || !window.confirm(`担当役割「${group.name}」を削除しますか?`)) return;
    setBusy(true);
    try {
      await api.deleteApproverGroup(tenantId, group.id);
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="group-form">
      <label>
        担当役割の名前
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例: 経理担当" />
      </label>
      <fieldset className="member-picks">
        <legend>担当する社員</legend>
        {members.map((m) => (
          <label key={m.user_id} className="check">
            <input
              type="checkbox"
              checked={userIds.includes(m.user_id)}
              onChange={(e) => setUserIds((ids) => (e.target.checked ? [...ids, m.user_id] : ids.filter((x) => x !== m.user_id)))}
            />
            {m.display_name}
          </label>
        ))}
      </fieldset>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="actions inline">
        <button type="button" onClick={save} disabled={busy || !name.trim()}>
          保存
        </button>
        <button type="button" className="secondary" onClick={onCancel} disabled={busy}>
          やめる
        </button>
        {group && (
          <button type="button" className="danger-text" onClick={remove} disabled={busy}>
            削除
          </button>
        )}
      </div>
    </div>
  );
}
