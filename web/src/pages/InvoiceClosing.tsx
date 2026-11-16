import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Billable, type Customer } from "../api";
import { jdate, yen } from "../format";
import { navigate } from "../router";
import { CustomerSelect } from "./QuoteEditor";

/** 月締めのまとめ請求。取引先と締め日を選ぶと、締め日までに納品した未請求の受注明細を集める */
export default function InvoiceClosing({ tenantId }: { tenantId: string }) {
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [customerId, setCustomerId] = useState("");
  const [closingDate, setClosingDate] = useState(""); // 空なら直近の締め日
  const [billable, setBillable] = useState<Billable | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dateFromServer = useRef(false); // 締め日の初期値をサーバーから受け取ったときは読み込み直さない

  useEffect(() => {
    api.customers(tenantId, true).then(setCustomers).catch((e: Error) => setError(e.message));
  }, [tenantId]);

  useEffect(() => {
    if (!customerId) {
      setBillable(null);
      return;
    }
    if (dateFromServer.current) {
      dateFromServer.current = false;
      return;
    }
    setLoading(true);
    setError("");
    api
      .billableLines(tenantId, customerId, closingDate || null)
      .then((b) => {
        setBillable(b);
        setSelected(new Set(b.lines.map((l) => l.order_line_id)));
        if (!closingDate) {
          dateFromServer.current = true;
          setClosingDate(b.closing_date);
        }
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [tenantId, customerId, closingDate]);

  const total = useMemo(
    () => (billable?.lines ?? []).filter((l) => selected.has(l.order_line_id)).reduce((sum, l) => sum + Number(l.amount), 0),
    [billable, selected],
  );

  function toggle(id: string) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function create() {
    if (!billable || selected.size === 0) return;
    setBusy(true);
    try {
      const invoice = await api.invoiceFromOrderLines(tenantId, {
        customer_id: customerId,
        closing_date: billable.closing_date,
        // 画面の並び(納品日順)のまま請求書の明細にする
        order_line_ids: billable.lines.filter((l) => selected.has(l.order_line_id)).map((l) => l.order_line_id),
      });
      navigate(`/invoices/${invoice.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <a className="back" href="#/invoices">
          請求の一覧
        </a>
        <h1>締め請求を作成</h1>
      </div>

      <div className="stack">
        <section className="panel">
          <div className="field-row">
            <CustomerSelect
              customers={customers}
              value={customerId}
              invalid={false}
              includeInactive /* 使用中止にした取引先にも、未請求の残りや未入金があれば処理できるようにする */
              onChange={(id) => {
                setCustomerId(id);
                setClosingDate("");
              }}
            />
            <label>
              締め日
              <input type="date" value={closingDate} disabled={!customerId} onChange={(e) => setClosingDate(e.target.value)} />
            </label>
          </div>
          {billable && (
            <p className="hint">
              今回の締め期間は {jdate(billable.period_start)}〜{jdate(billable.closing_date)}、お支払期限は {jdate(billable.due_date)} です。
              それより前に納品して、まだ請求していない明細も含めています。
            </p>
          )}
        </section>

        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}

        {customerId && (
          <section className="panel lines-panel">
            <h2>請求する明細</h2>
            {loading || !billable ? (
              <p className="placeholder">読み込み中…</p>
            ) : billable.lines.length === 0 ? (
              <p className="placeholder">締め日までに納品した、未請求の明細はありません。</p>
            ) : (
              <div className="table-wrap flat">
                <table>
                  <thead>
                    <tr>
                      <th scope="col" className="col-check">
                        <span className="visually-hidden">請求する</span>
                      </th>
                      <th scope="col">納品日</th>
                      <th scope="col">受注</th>
                      <th scope="col">品名</th>
                      <th scope="col" className="num">
                        金額(税抜)
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {billable.lines.map((l) => (
                      <tr key={l.order_line_id} className={selected.has(l.order_line_id) ? "" : "is-muted"}>
                        <td className="col-check">
                          <input
                            type="checkbox"
                            aria-label={`${l.description}を請求する`}
                            checked={selected.has(l.order_line_id)}
                            onChange={() => toggle(l.order_line_id)}
                          />
                        </td>
                        <td>{jdate(l.delivered_on)}</td>
                        <td>
                          <a href={`#/orders/${l.order_id}`}>{l.order_number}</a>
                          {l.order_title && <span className="honorific">{l.order_title}</span>}
                        </td>
                        <td>{l.description}</td>
                        <td className="num">{yen(l.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {billable && billable.missing_delivery_date > 0 && (
              <p className="notice-warn">
                納品日が入っていない明細が{billable.missing_delivery_date}件あり、対象にしていません。受注の画面で納品日を入力してください。
              </p>
            )}
            {billable && billable.lines.length > 0 && (
              <div className="closing-footer">
                <p>
                  {selected.size}件・税抜 <strong>{yen(total)}</strong>
                </p>
                <button type="button" onClick={create} disabled={busy || selected.size === 0}>
                  {busy ? "作成しています…" : "請求書を作成(作成中で保存)"}
                </button>
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
