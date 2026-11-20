import { useEffect, useState } from "react";
import { api, type OrderFilter, type OrderListItem } from "../api";
import { jdate, yen } from "../format";

const TABS: { value: OrderFilter; label: string }[] = [
  { value: "all", label: "すべて" },
  { value: "open", label: "未請求" },
  { value: "billed", label: "請求済" },
];

export default function OrderList({ tenantId }: { tenantId: string }) {
  const [filter, setFilter] = useState<OrderFilter>("all");
  const [orders, setOrders] = useState<OrderListItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setOrders(null);
    setError("");
    api.orders(tenantId, filter).then(setOrders).catch((e: Error) => setError(e.message));
  }, [tenantId, filter]);

  return (
    <>
      <div className="page-head">
        <h1>受注</h1>
        {orders && <p className="count">{orders.length}件</p>}
        <a className="button secondary-link" href="#/imports">
          注文書から取り込む
        </a>
        <a className="button" href="#/orders/new">
          受注を登録
        </a>
      </div>

      <div className="tabs" role="tablist" aria-label="受注の状態">
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

      {orders === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : orders.length === 0 ? (
        <p className="placeholder">
          {filter === "all"
            ? "受注はまだありません。発行済みの見積から「受注に変換」するか、「受注を登録」から直接登録してください。"
            : "この状態の受注はありません。"}
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">受注番号</th>
                <th scope="col">取引先</th>
                <th scope="col">件名</th>
                <th scope="col">受注日</th>
                <th scope="col">納期</th>
                <th scope="col">元の見積</th>
                <th scope="col" className="num">
                  合計(税込)
                </th>
                <th scope="col">状態</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td>
                    <a href={`#/orders/${o.id}`}>{o.order_number}</a>
                  </td>
                  <td>{o.customer_name}</td>
                  <td>{o.title ?? "―"}</td>
                  <td>{jdate(o.order_date)}</td>
                  <td>{jdate(o.delivery_due_date)}</td>
                  <td>{o.quote_number ?? "―"}</td>
                  <td className="num">{yen(o.total_amount)}</td>
                  <td>
                    <OrderStatusBadge status={o.status} />
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

export function OrderStatusBadge({ status, partial = false }: { status: "open" | "billed"; partial?: boolean }) {
  if (status === "billed") return <span className="badge is-ordered">請求済</span>;
  return <span className="badge is-issued">{partial ? "一部請求済" : "未請求"}</span>;
}
