import { useEffect, useState } from "react";
import { api, type RequestBox, type RequestListItem, type RequestStatus } from "../../api";
import { yen } from "../../format";
import { navigate } from "../../router";
import { REQUEST_STATUS_LABELS, RequestStatusBadge, shortDateTime } from "./common";

/**
 * 申請・承認の一覧。「承認待ち」(自分の番の申請、古い順)・「自分の申請」・「すべて」(管理者)を切り替える。
 * スマホでも読めるよう、表ではなく1件1行のカードで並べる
 */
export default function RequestList({ tenantId, box, isAdmin }: { tenantId: string; box: RequestBox; isAdmin: boolean }) {
  const [items, setItems] = useState<RequestListItem[] | null>(null);
  const [status, setStatus] = useState<RequestStatus | "">("");
  const [summary, setSummary] = useState<{ todo: number; returned: number } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.requestSummary(tenantId).then(setSummary).catch(() => setSummary(null));
  }, [tenantId, box]);

  useEffect(() => {
    setItems(null);
    setError("");
    api
      .requests(tenantId, box, status || undefined)
      .then(setItems)
      .catch((e: Error) => setError(e.message));
  }, [tenantId, box, status]);

  const tabs: { value: RequestBox; label: string; count?: number }[] = [
    { value: "todo", label: "承認待ち", count: summary?.todo },
    { value: "mine", label: "自分の申請", count: summary?.returned },
    ...(isAdmin ? [{ value: "all" as const, label: "すべての申請" }] : []),
  ];
  const statuses = (Object.keys(REQUEST_STATUS_LABELS) as RequestStatus[]).filter((s) => box === "mine" || s !== "draft");

  return (
    <>
      <div className="page-head">
        <h1>申請・承認</h1>
        {items && <p className="count">{items.length}件</p>}
        <a className="button" href="#/requests/new">
          申請を作成
        </a>
      </div>

      <div className="tabs" role="tablist" aria-label="申請の一覧">
        {tabs.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={box === tab.value}
            className="tab"
            onClick={() => {
              setStatus("");
              navigate(`/requests?box=${tab.value}`);
            }}
          >
            {tab.label}
            {!!tab.count && (
              <span className={`tab-count${tab.value === "todo" ? " is-alert" : ""}`} aria-label={`${tab.count}件`}>
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {box !== "todo" && (
        <label className="switcher filter">
          <span>状態</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as RequestStatus | "")}>
            <option value="">すべて</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {REQUEST_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
      )}

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {items === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : items.length === 0 ? (
        <p className="placeholder">
          {box === "todo"
            ? "あなたの承認待ちの申請はありません。"
            : box === "mine" && !status
              ? "申請はまだありません。「申請を作成」から経費精算・購買申請・休暇申請などを出せます。"
              : "この条件の申請はありません。"}
        </p>
      ) : (
        <ul className="request-list">
          {items.map((r) => (
            <li key={r.id}>
              <a className={`request-card${r.is_my_turn ? " is-my-turn" : ""}`} href={`#/requests/${r.id}`}>
                <span className="request-card-main">
                  <span className="request-meta">
                    <span className="type-tag">{r.type_name}</span>
                    <span className="request-number">{r.request_number ?? "(未申請)"}</span>
                  </span>
                  <strong className="request-title">{r.title}</strong>
                  <span className="request-sub">
                    {r.applicant_name}
                    <span aria-hidden="true"> ・ </span>
                    {r.submitted_at ? `${shortDateTime(r.submitted_at)} 申請` : `${shortDateTime(r.updated_at)} 保存`}
                    {r.amount !== null && (
                      <>
                        <span aria-hidden="true"> ・ </span>
                        {yen(r.amount)}
                      </>
                    )}
                  </span>
                </span>
                <span className="request-card-side">
                  <RequestStatusBadge status={r.status} />
                  {r.status === "pending" && (
                    <span className="request-step">{r.is_my_turn ? "あなたの番です" : `${r.current_step_label}の承認待ち`}</span>
                  )}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
