import { useEffect, useState } from "react";
import { api, type Quote } from "../api";
import TotalsList from "../components/TotalsList";
import { useCompany } from "../company";
import { decimal, jdate, TAX_OPTIONS, yen } from "../format";
import { navigate } from "../router";
import QuoteEditor from "./QuoteEditor";
import { StatusBadge } from "./QuoteList";

/** 見積1件の画面。作成中なら編集画面、発行後は詳細画面を出す */
export default function QuotePage({ tenantId, id }: { tenantId: string; id: string }) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setQuote(null);
    setError("");
    api.quote(tenantId, id).then(setQuote).catch((e: Error) => setError(e.message));
  }, [tenantId, id]);

  if (error) {
    return (
      <>
        <a className="back" href="#/quotes">
          見積の一覧
        </a>
        <p className="alert" role="alert">
          {error}
        </p>
      </>
    );
  }
  if (!quote) return <p className="placeholder">読み込み中…</p>;
  if (quote.status === "draft") return <QuoteEditor tenantId={tenantId} quote={quote} onIssued={setQuote} />;
  return <QuoteDetail tenantId={tenantId} quote={quote} onChange={setQuote} />;
}

const TAX_LABEL = Object.fromEntries(TAX_OPTIONS.map((o) => [o.value, o.label]));

function QuoteDetail({ tenantId, quote, onChange }: { tenantId: string; quote: Quote; onChange: (q: Quote) => void }) {
  const { taxExempt } = useCompany();
  // 免税事業者に切り替える前に発行した見積は、消費税を表示したままにする
  const exempt = taxExempt && quote.tax_summaries.every((s) => Number(s.tax_rate) === 0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function openPdf() {
    try {
      await api.openQuotePdf(tenantId, quote.id);
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function convert() {
    const warning = quote.is_expired
      ? "この見積は有効期限を過ぎています。条件をお客様に確認済みの場合だけ、受注に変換してください。\n\n受注に変換しますか？"
      : "この見積を受注に変換しますか？";
    if (!window.confirm(warning)) return;
    setBusy(true);
    try {
      const order = await api.convertQuote(tenantId, quote.id);
      navigate(`/orders/${order.id}`);
    } catch (e) {
      setMessage((e as Error).message);
      setBusy(false);
    }
  }

  async function lose() {
    if (!window.confirm("この見積を失注にしますか？")) return;
    setBusy(true);
    try {
      onChange(await api.loseQuote(tenantId, quote.id));
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <a className="back" href="#/quotes">
          見積の一覧
        </a>
        <h1>{quote.quote_number}</h1>
        <StatusBadge status={quote.status} expired={quote.is_expired} />
      </div>

      <div className="editor">
        <div className="editor-main">
          <section className="panel">
            <dl className="facts">
              <div>
                <dt>取引先</dt>
                <dd>
                  {quote.customer_name} {quote.customer_honorific}
                </dd>
              </div>
              <div>
                <dt>件名</dt>
                <dd>{quote.title ?? "―"}</dd>
              </div>
              <div>
                <dt>発行日</dt>
                <dd>{jdate(quote.issue_date)}</dd>
              </div>
              <div>
                <dt>有効期限</dt>
                <dd>{jdate(quote.valid_until)}</dd>
              </div>
            </dl>
          </section>

          <section className="panel lines-panel">
            <h2>明細</h2>
            <div className="table-wrap flat">
              <table className="lines readonly">
                <thead>
                  <tr>
                    <th scope="col" className="col-no">
                      行
                    </th>
                    <th scope="col">品名</th>
                    <th scope="col" className="num">
                      数量
                    </th>
                    <th scope="col">単位</th>
                    <th scope="col" className="num">
                      単価(税抜)
                    </th>
                    {!exempt && <th scope="col">税率</th>}
                    <th scope="col" className="num">
                      金額
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {quote.lines.map((l) => (
                    <tr key={l.line_no}>
                      <td className="col-no">{l.line_no}</td>
                      <td>{l.description}</td>
                      <td className="num">{decimal(l.quantity)}</td>
                      <td>{l.unit ?? ""}</td>
                      <td className="num">{decimal(l.unit_price)}</td>
                      {!exempt && <td>{TAX_LABEL[l.tax_category]}</td>}
                      <td className="num">{yen(l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {quote.remarks && (
            <section className="panel">
              <h2>備考</h2>
              <p className="remarks">{quote.remarks}</p>
            </section>
          )}
        </div>

        <aside className="editor-side">
          <div className="panel totals">
            <h2>合計</h2>
            <TotalsList
              summaries={quote.tax_summaries}
              subtotal={quote.subtotal_amount}
              tax={quote.tax_amount}
              total={quote.total_amount}
              exempt={exempt}
            />

            <div className="actions">
              {quote.status === "issued" && (
                <button type="button" onClick={convert} disabled={busy}>
                  受注に変換
                </button>
              )}
              {quote.order_id && (
                <a className="button" href={`#/orders/${quote.order_id}`}>
                  受注を開く
                </a>
              )}
              <button type="button" className="secondary" onClick={openPdf}>
                見積書PDFを開く
              </button>
              {quote.status === "issued" && (
                <button type="button" className="secondary" onClick={lose} disabled={busy}>
                  失注にする
                </button>
              )}
            </div>
            <p className="hint">発行済みの見積は変更できません。内容を変えるときは新しい見積を作成してください。</p>

            {message && (
              <p className="alert" role="alert">
                {message}
              </p>
            )}
          </div>
        </aside>
      </div>
    </>
  );
}
