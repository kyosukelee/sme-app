import { useCallback, useEffect, useState } from "react";
import { api, type ActivityLogItem, type ChangeLogItem, type LogFilters, type LogQuery, type Member } from "../api";
import { jdatetime } from "../format";

type Tab = "changes" | "activities";

/**
 * 操作の履歴(管理者だけ)。
 * 変更の履歴: 誰が・いつ・どの書類や設定の・どの項目を・何から何に変えたか(DB のトリガーで自動記録)。
 * 閲覧・書き出しの記録: 控えや添付を開いた、データ一式や仕訳CSVを持ち出した。
 * どちらも消したり書き換えたりできない
 */
export default function OperationLogs({ tenantId }: { tenantId: string }) {
  const [tab, setTab] = useState<Tab>("changes");
  const [filters, setFilters] = useState<LogFilters | null>(null);
  const [people, setPeople] = useState<Member[]>([]);
  const [query, setQuery] = useState<LogQuery>({});
  const [recordLabel, setRecordLabel] = useState("");
  const [changes, setChanges] = useState<ChangeLogItem[] | null>(null);
  const [activities, setActivities] = useState<ActivityLogItem[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.logFilters(tenantId), api.members(tenantId, true)])
      .then(([f, m]) => {
        setFilters(f);
        setPeople(m);
      })
      .catch((e: Error) => setError(e.message));
  }, [tenantId]);

  const load = useCallback(
    async (before?: number) => {
      const q = { ...query, before_id: before };
      if (tab === "changes") {
        const page = await api.changeLogs(tenantId, q);
        setChanges((list) => (before && list ? [...list, ...page.items] : page.items));
        setNext(page.next_before_id);
      } else {
        const page = await api.activityLogs(tenantId, q);
        setActivities((list) => (before && list ? [...list, ...page.items] : page.items));
        setNext(page.next_before_id);
      }
    },
    [tenantId, tab, query],
  );

  useEffect(() => {
    if (!filters) return;
    setChanges(null);
    setActivities(null);
    setError("");
    load().catch((e: Error) => setError(e.message));
  }, [load, filters]);

  function set(key: keyof LogQuery, value: string) {
    setQuery((q) => ({ ...q, [key]: value || undefined }));
  }

  function switchTab(t: Tab) {
    setTab(t);
    setQuery((q) => ({ date_from: q.date_from, date_to: q.date_to, actor_id: q.actor_id }));
    setRecordLabel("");
  }

  function onlyRecord(item: ChangeLogItem) {
    setQuery({ record_id: item.record_id, table: item.table_name });
    setRecordLabel(`${item.table_label}「${item.record_label}」`);
    window.scrollTo({ top: 0 });
  }

  async function more() {
    if (next === null) return;
    setLoadingMore(true);
    try {
      await load(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }

  const items = tab === "changes" ? changes : activities;

  return (
    <>
      <div className="page-head">
        <h1>操作の履歴</h1>
      </div>
      <p className="lead">
        誰が・いつ・何を変えたか(変更の履歴)と、控えや添付を開いた・データを持ち出した記録を確かめられます。記録は自動で残り、消したり書き換えたりできません。
      </p>

      <div className="tabs" role="tablist" aria-label="履歴の種類">
        <button type="button" role="tab" className="tab" aria-selected={tab === "changes"} onClick={() => switchTab("changes")}>
          変更の履歴
        </button>
        <button type="button" role="tab" className="tab" aria-selected={tab === "activities"} onClick={() => switchTab("activities")}>
          閲覧・書き出しの記録
        </button>
      </div>

      {filters && (
        <div className="log-filters">
          <label>
            期間
            <span className="period-inputs">
              <input type="date" aria-label="期間の始まり" value={query.date_from ?? ""} onChange={(e) => set("date_from", e.target.value)} />
              <span aria-hidden="true">〜</span>
              <input type="date" aria-label="期間の終わり" value={query.date_to ?? ""} onChange={(e) => set("date_to", e.target.value)} />
            </span>
          </label>
          {tab === "changes" ? (
            <label>
              対象
              <select
                value={query.table ?? ""}
                onChange={(e) => {
                  setRecordLabel("");
                  setQuery((q) => ({ ...q, table: e.target.value || undefined, record_id: undefined }));
                }}
              >
                <option value="">すべて</option>
                {filters.tables.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label>
              操作
              <select value={query.action ?? ""} onChange={(e) => set("action", e.target.value)}>
                <option value="">すべて</option>
                {filters.actions.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {tab === "changes" && !query.table && (
            <label className="check inline-check">
              <input
                type="checkbox"
                checked={!!query.include_lines}
                onChange={(e) => setQuery((q) => ({ ...q, include_lines: e.target.checked || undefined }))}
              />
              明細の行の変更も表示する
            </label>
          )}
          <label>
            操作した人
            <select value={query.actor_id ?? ""} onChange={(e) => set("actor_id", e.target.value)}>
              <option value="">すべて</option>
              {people.map((p) => (
                <option key={p.user_id} value={p.user_id}>
                  {p.display_name}
                  {p.is_active ? "" : "(使用中止)"}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {recordLabel && (
        <p className="filter-chip">
          {recordLabel}の履歴だけを表示しています
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setRecordLabel("");
              setQuery((q) => ({ ...q, record_id: undefined, table: undefined }));
            }}
          >
            解除
          </button>
        </p>
      )}

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {!error && items === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : items && items.length === 0 ? (
        <p className="placeholder">この条件の記録はありません。</p>
      ) : tab === "changes" ? (
        <ol className="log-list">
          {changes?.map((c) => (
            <ChangeItem key={c.id} item={c} onOnlyRecord={() => onlyRecord(c)} />
          ))}
        </ol>
      ) : (
        <ol className="log-list">
          {activities?.map((a) => (
            <li key={a.id} className="log-item">
              <div className="log-head">
                <time dateTime={a.created_at}>{jdatetime(a.created_at)}</time>
                <span className="log-actor">{a.actor_name ?? "(不明)"}</span>
                <span className={`badge log-${a.action === "export_all" || a.action === "download_journal" ? "export" : "open"}`}>
                  {a.action_label}
                </span>
              </div>
              <p className="log-target">{a.summary}</p>
            </li>
          ))}
        </ol>
      )}

      {next !== null && (
        <button type="button" className="secondary load-more" onClick={more} disabled={loadingMore}>
          {loadingMore ? "読み込んでいます…" : "さらに読み込む"}
        </button>
      )}
    </>
  );
}

function ChangeItem({ item, onOnlyRecord }: { item: ChangeLogItem; onOnlyRecord: () => void }) {
  const shown = item.changes.slice(0, 4).map((c) => c.label);
  const rest = item.changes.length - shown.length;
  return (
    <li className="log-item">
      <div className="log-head">
        <time dateTime={item.created_at}>{jdatetime(item.created_at)}</time>
        <span className="log-actor">{item.actor_name ?? "(システム)"}</span>
        <span className={`badge log-${item.action}`}>{item.action_label}</span>
      </div>
      <p className="log-target">
        <span className="type-tag">{item.table_label}</span>
        {item.record_label}
      </p>
      {item.changes.length > 0 && (
        <details className="log-detail">
          <summary>
            {item.action === "update" ? "変えた項目: " : "内容: "}
            {shown.join("・")}
            {rest > 0 && ` ほか${rest}項目`}
          </summary>
          <div className="table-wrap flat">
            <table className="diff-table">
              <thead>
                <tr>
                  <th scope="col">項目</th>
                  {item.action !== "insert" && <th scope="col">変更前</th>}
                  {item.action !== "delete" && <th scope="col">{item.action === "insert" ? "内容" : "変更後"}</th>}
                </tr>
              </thead>
              <tbody>
                {item.changes.map((c) => (
                  <tr key={c.field}>
                    <th scope="row">{c.label}</th>
                    {item.action !== "insert" && <td className="before">{c.before ?? "(なし)"}</td>}
                    {item.action !== "delete" && <td className="after">{c.after ?? "(なし)"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="log-meta">
            <button type="button" className="link-button" onClick={onOnlyRecord}>
              この対象の履歴だけを見る
            </button>
            {item.request_id && <small>リクエスト番号 {item.request_id}</small>}
          </p>
        </details>
      )}
    </li>
  );
}
