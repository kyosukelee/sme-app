import { useEffect, useState } from "react";
import { api, type RequestDetail, type RequestEvent } from "../../api";
import { jdatetime, yen } from "../../format";
import { navigate } from "../../router";
import { fieldValue, fileSize, RequestStatusBadge, RouteView, shortDateTime } from "./common";

const EVENT_TEXT: Record<RequestEvent["action"], string> = {
  submit: "申請しました",
  resubmit: "直して再申請しました",
  approve: "承認しました",
  return: "差し戻しました",
  reject: "却下しました",
  withdraw: "取り下げました",
  comment: "コメントしました",
  skip: "省略しました",
  complete: "すべての承認が終わりました",
};

/**
 * 申請の詳細。承認ルートの進み具合、内容、添付、履歴とコメントを1画面で見せる。
 * 自分の番なら承認・差戻し・却下、申請者なら直す・申請する・取り下げるを出す
 */
export default function RequestPage({ tenantId, id }: { tenantId: string; id: string }) {
  const [request, setRequest] = useState<RequestDetail | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  useEffect(() => {
    setRequest(null);
    api
      .request(tenantId, id)
      .then(setRequest)
      .catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId, id]);

  if (!request) {
    return (
      <>
        <a className="back" href="#/requests">
          申請・承認の一覧
        </a>
        {message ? (
          <p className="alert" role="alert">
            {message.text}
          </p>
        ) : (
          <p className="placeholder">読み込み中…</p>
        )}
      </>
    );
  }
  const r = request;
  const p = r.permissions;

  async function run(action: "submit" | "approve" | "return" | "reject" | "withdraw" | "comments", done: string) {
    const text = comment.trim();
    if ((action === "return" || action === "reject") && !text) {
      setMessage({ kind: "error", text: "差し戻し・却下のときは、理由をコメント欄に入力してください" });
      return;
    }
    if (action === "withdraw" && !window.confirm("この申請を取り下げますか?取り下げた申請は元に戻せません。")) return;
    if (action === "reject" && !window.confirm("この申請を却下しますか?却下すると申請者は再申請できません。")) return;
    setBusy(true);
    setMessage(null);
    try {
      const next = await api.requestAction(tenantId, r.id, action, action === "submit" ? null : text || null);
      setRequest(next);
      setComment("");
      setMessage({ kind: "done", text: done });
      window.dispatchEvent(new Event("requests-changed")); // メニューの件数を更新する
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const finished = r.status === "approved" || r.status === "rejected" || r.status === "withdrawn" ? r.status : null;
  const lastReturn = r.status === "returned" ? [...r.events].reverse().find((e) => e.action === "return") : null;

  return (
    <>
      <div className="page-head">
        <a className="back" href={`#/requests?box=${r.is_my_turn ? "todo" : "mine"}`}>
          申請・承認の一覧
        </a>
        <h1>{r.title}</h1>
        <RequestStatusBadge status={r.status} />
        <p className="count">
          {r.type_name}
          {r.request_number && ` ・ ${r.request_number}`}
        </p>
      </div>

      {r.is_my_turn && (
        <p className="notice my-turn" role="status">
          あなたの承認待ちです。内容を確かめて、承認・差戻し・却下のいずれかを選んでください。
        </p>
      )}
      {lastReturn && (
        <div className="notice-warn return-reason" role="note">
          <strong>{lastReturn.actor?.display_name}さんから差し戻されました</strong>
          <p>{lastReturn.comment}</p>
        </div>
      )}

      <section className="panel route-panel">
        <h2>承認ルート</h2>
        {r.round === 0 ? (
          <p className="hint">申請すると、承認ルートが決まります。</p>
        ) : (
          <RouteView steps={r.steps} applicant={r.applicant_name} submittedAt={r.submitted_at} finished={finished} />
        )}
        {r.round > 1 && <p className="hint">差戻し後の再申請のため、1段目からやり直しています({r.round}回目の申請)。</p>}
      </section>

      <div className="request-layout">
        <div className="stack">
          <section className="panel">
            <h2>申請の内容</h2>
            <dl className="facts">
              <div>
                <dt>申請者</dt>
                <dd>{r.applicant_name}</dd>
              </div>
              <div>
                <dt>申請日時</dt>
                <dd>{r.submitted_at ? jdatetime(r.submitted_at) : "未申請"}</dd>
              </div>
              {r.amount_mode !== "none" && (
                <div>
                  <dt>{r.amount_label}</dt>
                  <dd className="amount">{r.amount !== null ? yen(r.amount) : "―"}</dd>
                </div>
              )}
              {r.form_schema.map((f) => (
                <div key={f.key} className={f.type === "textarea" ? "wide" : undefined}>
                  <dt>{f.label}</dt>
                  <dd className={f.type === "textarea" ? "remarks" : undefined}>{fieldValue(f, r.form_data[f.key])}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="panel">
            <h2>添付</h2>
            {r.attachments.length === 0 ? (
              <p className="placeholder">添付はありません。</p>
            ) : (
              <ul className="attachment-list">
                {r.attachments.map((a) => (
                  <li key={a.id}>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => api.openAttachment(tenantId, r.id, a).catch((e: Error) => setMessage({ kind: "error", text: e.message }))}
                    >
                      {a.file_name}
                    </button>
                    <small>{fileSize(a.size_bytes)}</small>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <h2>履歴とコメント</h2>
            {r.events.length === 0 ? (
              <p className="placeholder">まだ申請していません。</p>
            ) : (
              <ol className="timeline">
                {r.events.map((e) => (
                  <li key={e.id} className={`is-${e.action}`}>
                    <p className="timeline-head">
                      {(e.actor || e.action === "skip") && <strong>{e.actor?.display_name ?? e.step_label}</strong>}
                      <span>
                        {e.action === "skip" ? `の段を${e.comment ?? "省略しました"}` : EVENT_TEXT[e.action]}
                        {e.step_label && ["approve", "return", "reject"].includes(e.action) && <small>({e.step_label})</small>}
                      </span>
                      <time dateTime={e.created_at}>{shortDateTime(e.created_at)}</time>
                    </p>
                    {e.comment && e.action !== "skip" && <p className="timeline-comment">{e.comment}</p>}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>

        <aside className="stack request-actions">
          {(p.can_act || p.can_comment) && (
            <section className="panel">
              <h2>{p.can_act ? "承認する" : "コメント"}</h2>
              <label>
                コメント{p.can_act ? "(差戻し・却下のときは必須)" : ""}
                <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} maxLength={1000} />
              </label>
              {p.can_act ? (
                <div className="actions decision">
                  <button type="button" onClick={() => run("approve", "承認しました")} disabled={busy}>
                    承認する
                  </button>
                  <button type="button" className="secondary" onClick={() => run("return", "差し戻しました")} disabled={busy}>
                    差し戻す
                  </button>
                  <button type="button" className="danger-text" onClick={() => run("reject", "却下しました")} disabled={busy}>
                    却下する
                  </button>
                </div>
              ) : null}
              {p.can_comment && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => run("comments", "コメントを残しました")}
                  disabled={busy || !comment.trim()}
                >
                  コメントだけ残す
                </button>
              )}
            </section>
          )}

          {(p.can_edit || p.can_submit || p.can_withdraw) && (
            <section className="panel">
              <h2>申請者の操作</h2>
              <div className="actions">
                {p.can_submit && (
                  <button type="button" onClick={() => run("submit", r.status === "returned" ? "再申請しました" : "申請しました")} disabled={busy}>
                    {r.status === "returned" ? "このまま再申請する" : "申請する"}
                  </button>
                )}
                {p.can_edit && (
                  <button type="button" className="secondary" onClick={() => navigate(`/requests/${r.id}/edit`)} disabled={busy}>
                    内容を直す
                  </button>
                )}
                {p.can_withdraw && (
                  <button type="button" className="danger-text" onClick={() => run("withdraw", "取り下げました")} disabled={busy}>
                    取り下げる
                  </button>
                )}
              </div>
            </section>
          )}

          {message && (
            <p className={message.kind === "done" ? "notice" : "alert"} role={message.kind === "done" ? "status" : "alert"}>
              {message.text}
            </p>
          )}
        </aside>
      </div>
    </>
  );
}
