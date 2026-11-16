import { useEffect, useState } from "react";
import { api, type InvoiceFilter, type InvoiceListItem } from "../api";
import { jdate, yen } from "../format";

const TABS: { value: InvoiceFilter; label: string }[] = [
  { value: "all", label: "すべて" },
  { value: "draft", label: "作成中" },
  { value: "unpaid", label: "未入金" },
  { value: "paid", label: "入金済" },
  { value: "void", label: "取消" },
];

export default function InvoiceList({ tenantId }: { tenantId: string }) {
  const [filter, setFilter] = useState<InvoiceFilter>("all");
  const [invoices, setInvoices] = useState<InvoiceListItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setInvoices(null);
    setError("");
    api.invoices(tenantId, filter).then(setInvoices).catch((e: Error) => setError(e.message));
  }, [tenantId, filter]);

  return (
    <>
      <div className="page-head">
        <h1>請求</h1>
        {invoices && <p className="count">{invoices.length}件</p>}
        <div className="head-actions">
          <a className="button secondary-link" href="#/invoices/new">
            請求書を作成
          </a>
          <a className="button" href="#/invoices/closing">
            締め請求を作成
          </a>
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="請求書の状態">
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

      {invoices === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : invoices.length === 0 ? (
        <p className="placeholder">
          {filter === "all"
            ? "請求書はまだありません。受注の画面の「請求書を作成」か、「締め請求を作成」から作ってください。"
            : "この状態の請求書はありません。"}
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">請求番号</th>
                <th scope="col">取引先</th>
                <th scope="col">件名</th>
                <th scope="col">請求日</th>
                <th scope="col">支払期限</th>
                <th scope="col" className="num">
                  請求額
                </th>
                <th scope="col" className="num">
                  残額
                </th>
                <th scope="col">状態</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((i) => (
                <tr key={i.id}>
                  <td>
                    <a href={`#/invoices/${i.id}`}>{i.invoice_number ?? "(未発行)"}</a>
                    {i.version > 1 && <span className="honorific">第{i.version}版</span>}
                  </td>
                  <td>{i.customer_name}</td>
                  <td>{i.title ?? "―"}</td>
                  <td>{jdate(i.issue_date)}</td>
                  <td>{jdate(i.due_date)}</td>
                  <td className="num">
                    {yen(i.withholding ? i.net_amount : i.total_amount)}
                    {i.withholding && <small className="after">源泉後</small>}
                  </td>
                  <td className="num">
                    {i.status === "issued" ? yen(i.balance) : Number(i.balance) < 0 ? `過入金 ${yen(-Number(i.balance))}` : "―"}
                  </td>
                  <td>
                    <InvoiceStatusBadge invoice={i} />
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

export function InvoiceStatusBadge({ invoice }: { invoice: Pick<InvoiceListItem, "status" | "is_overdue" | "paid_amount" | "balance"> }) {
  if (invoice.status === "draft") return <span className="badge is-draft">作成中</span>;
  if (invoice.status === "paid" && Number(invoice.balance) < 0) return <span className="badge is-expired">過入金</span>;
  if (invoice.status === "paid") return <span className="badge is-ordered">入金済</span>;
  if (invoice.status === "void") return <span className="badge is-lost">取消</span>;
  if (invoice.is_overdue) return <span className="badge is-expired">期限超過</span>;
  if (Number(invoice.paid_amount) > 0) return <span className="badge is-issued">一部入金</span>;
  return <span className="badge is-issued">未入金</span>;
}
