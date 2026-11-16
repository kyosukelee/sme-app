import { useEffect, useState } from "react";
import { api, type QuoteFilter, type QuoteListItem } from "../api";
import { jdate, yen } from "../format";

const TABS: { value: QuoteFilter; label: string }[] = [
  { value: "all", label: "すべて" },
  { value: "draft", label: "作成中" },
  { value: "issued", label: "発行済" },
  { value: "ordered", label: "受注済" },
  { value: "closed", label: "失注・期限切れ" },
];

export default function QuoteList({ tenantId }: { tenantId: string }) {
  const [filter, setFilter] = useState<QuoteFilter>("all");
  const [quotes, setQuotes] = useState<QuoteListItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setQuotes(null);
    setError("");
    api.quotes(tenantId, filter).then(setQuotes).catch((e: Error) => setError(e.message));
  }, [tenantId, filter]);

  return (
    <>
      <div className="page-head">
        <h1>見積</h1>
        {quotes && <p className="count">{quotes.length}件</p>}
        <a className="button" href="#/quotes/new">
          見積を作成
        </a>
      </div>

      <div className="tabs" role="tablist" aria-label="見積の状態">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={filter === tab.value}
            className="tab"
            onClick={() => setFilter(tab.value)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {quotes === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : quotes.length === 0 ? (
        <p className="placeholder">
          {filter === "all" ? "見積はまだありません。「見積を作成」から最初の1件を作ってください。" : "この状態の見積はありません。"}
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">見積番号</th>
                <th scope="col">取引先</th>
                <th scope="col">件名</th>
                <th scope="col">発行日</th>
                <th scope="col">有効期限</th>
                <th scope="col" className="num">
                  合計(税込)
                </th>
                <th scope="col">状態</th>
              </tr>
            </thead>
            <tbody>
              {quotes.map((q) => (
                <tr key={q.id}>
                  <td>
                    <a href={`#/quotes/${q.id}`}>{q.quote_number ?? "(未発行)"}</a>
                  </td>
                  <td>{q.customer_name}</td>
                  <td>{q.title ?? "―"}</td>
                  <td>{jdate(q.issue_date)}</td>
                  <td>{jdate(q.valid_until)}</td>
                  <td className="num">{yen(q.total_amount)}</td>
                  <td>
                    <StatusBadge status={q.status} expired={q.is_expired} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

const STATUS_LABELS = { draft: "作成中", issued: "発行済", ordered: "受注済", lost: "失注" } as const;

export function StatusBadge({ status, expired }: { status: keyof typeof STATUS_LABELS; expired: boolean }) {
  if (expired) return <span className="badge is-expired">期限切れ</span>;
  return <span className={`badge is-${status}`}>{STATUS_LABELS[status]}</span>;
}
