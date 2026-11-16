import { useEffect, useState } from "react";
import { api, type Customer, type Order, type OrderInput } from "../api";
import LineTable from "../components/LineTable";
import TotalsList from "../components/TotalsList";
import { jdatetime, todayIso } from "../format";
import { blankRow, calcFromDocument, findLineProblem, isBlank, rowsFromDocument, toLine, type Row, type SavedLine } from "../lines";
import { navigate } from "../router";
import { useCompany } from "../company";
import { useLiveCalc } from "../useLiveCalc";
import { OrderStatusBadge } from "./OrderList";
import { CustomerSelect } from "./QuoteEditor";

/** 受注1件の画面。id がなければ新規登録 */
export default function OrderPage({ tenantId, id }: { tenantId: string; id?: string }) {
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    setOrder(null);
    setError("");
    api.order(tenantId, id).then(setOrder).catch((e: Error) => setError(e.message));
  }, [tenantId, id]);

  if (error) {
    return (
      <>
        <a className="back" href="#/orders">
          受注の一覧
        </a>
        <p className="alert" role="alert">
          {error}
        </p>
      </>
    );
  }
  if (id && !order) return <p className="placeholder">読み込み中…</p>;
  return <OrderEditor key={order?.id ?? "new"} tenantId={tenantId} initial={order ?? undefined} />;
}

type Busy = "" | "save" | "preview" | "acknowledge" | "delete" | "invoice";

/** 請求書(作成中を含む)に載った行は変更できない */
const lockOf = (line: SavedLine) => (line.billed ? "all" : "none");

const INVOICE_STATUS: Record<string, string> = { draft: "作成中", issued: "発行済", paid: "入金済" };

function OrderEditor({ tenantId, initial }: { tenantId: string; initial?: Order }) {
  const { taxExempt } = useCompany();
  const [order, setOrder] = useState<Order | undefined>(initial);
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [customerId, setCustomerId] = useState(initial?.customer_id ?? "");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [remarks, setRemarks] = useState(initial?.remarks ?? "");
  const [orderDate, setOrderDate] = useState(initial?.order_date ?? todayIso());
  const [dueDate, setDueDate] = useState(initial?.delivery_due_date ?? "");
  const [rows, setRows] = useState<Row[]>(() => (initial?.lines.length ? rowsFromDocument(initial, lockOf) : [blankRow(), blankRow()]));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<Busy>("");
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  // 請求済みの受注は変更できない。一部だけ請求した受注は、請求書に載った行だけが固定になる
  const invoiced = (order?.invoices.length ?? 0) > 0;
  const readOnly = order?.status === "billed";
  const { calc, setCalc, amountByKey } = useLiveCalc(
    tenantId,
    rows,
    orderDate || null,
    initial ? calcFromDocument(initial, rows) : null,
    (text) => setMessage({ kind: "error", text }),
  );

  useEffect(() => {
    api.customers(tenantId, true).then(setCustomers).catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  function change(patch: () => void) {
    patch();
    setDirty(true);
    setMessage(null);
  }

  function collect(): OrderInput | null {
    setShowErrors(true);
    const problem = !customerId ? "取引先を選んでください。" : !orderDate ? "受注日を入力してください。" : findLineProblem(rows);
    if (problem) {
      setMessage({ kind: "error", text: problem });
      return null;
    }
    return {
      customer_id: customerId,
      title: title.trim() || null,
      remarks: remarks.trim() || null,
      order_date: orderDate,
      delivery_due_date: dueDate || null,
      lines: rows
        .filter((r) => !isBlank(r))
        .map((r) => ({ ...toLine(r), line_id: r.line_id, quote_line_id: r.quote_line_id, delivered_on: r.delivered_on || null })),
    };
  }

  function applySaved(saved: Order) {
    const savedRows = rowsFromDocument(saved, lockOf);
    setOrder(saved);
    setRows(savedRows);
    setCalc(calcFromDocument(saved, savedRows));
    setTitle(saved.title ?? "");
    setRemarks(saved.remarks ?? "");
    setDirty(false);
  }

  async function save(input: OrderInput): Promise<Order> {
    const saved = order ? await api.updateOrder(tenantId, order.id, input) : await api.createOrder(tenantId, input);
    applySaved(saved);
    return saved;
  }

  /** 未保存の変更があれば保存してから、保存済みの受注を返す */
  async function ensureSaved(): Promise<Order | null> {
    if (order && !dirty) return order;
    const input = collect();
    return input ? save(input) : null;
  }

  async function handleSave() {
    const input = collect();
    if (!input) return;
    setBusy("save");
    try {
      const saved = await save(input);
      if (!initial) {
        navigate(`/orders/${saved.id}`);
        return;
      }
      setMessage({ kind: "done", text: "変更を保存しました" });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function handlePreview() {
    if (!readOnly && !collect()) return;
    const tab = window.open("", "_blank");
    setBusy("preview");
    try {
      const saved = readOnly ? order! : await ensureSaved();
      if (!saved) throw new Error("入力内容を確認してください。");
      await api.openAcknowledgmentPdf(tenantId, saved.id, { preview: true }, tab);
    } catch (e) {
      tab?.close();
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function handleAcknowledge() {
    if (!readOnly && !collect()) return;
    const next = (order?.acknowledgments.length ?? 0) + 1;
    if (!window.confirm(`いまの内容で注文請書(第${next}版)を発行し、控えを保存します。発行しますか？`)) return;
    const tab = window.open("", "_blank");
    setBusy("acknowledge");
    try {
      const saved = readOnly ? order! : await ensureSaved();
      if (!saved) throw new Error("入力内容を確認してください。");
      const issued = await api.issueAcknowledgment(tenantId, saved.id);
      setOrder(issued);
      await api.openAcknowledgmentPdf(tenantId, issued.id, {}, tab);
      setMessage({ kind: "done", text: `注文請書(第${next}版)を発行しました` });
    } catch (e) {
      tab?.close();
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function handleDelete() {
    if (!order) return;
    const note = order.quote_number ? `\n見積 ${order.quote_number} は「発行済」に戻ります。` : "";
    if (!window.confirm(`この受注を削除しますか？${note}`)) return;
    setBusy("delete");
    try {
      await api.deleteOrder(tenantId, order.id);
      navigate(order.quote_id ? `/quotes/${order.quote_id}` : "/orders");
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
      setBusy("");
    }
  }

  async function handleInvoice() {
    if (!order) return;
    setBusy("invoice");
    try {
      const invoice = await api.invoiceFromOrder(tenantId, order.id);
      navigate(`/invoices/${invoice.id}`);
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
      setBusy("");
    }
  }

  async function openAcknowledgment(version: number) {
    try {
      await api.openAcknowledgmentPdf(tenantId, order!.id, { version });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    }
  }

  const result = calc?.result;
  const acknowledgments = order?.acknowledgments ?? [];

  return (
    <>
      <div className="page-head">
        <a className="back" href="#/orders">
          受注の一覧
        </a>
        <h1>{order ? order.order_number : "受注を登録"}</h1>
        {order && (
          <OrderStatusBadge status={order.status} partial={order.invoices.some((i) => i.status === "issued" || i.status === "paid")} />
        )}
        {order?.quote_id && (
          <a className="source-link" href={`#/quotes/${order.quote_id}`}>
            見積 {order.quote_number} から変換
          </a>
        )}
        {dirty && <p className="count">未保存の変更があります</p>}
      </div>

      {order?.status === "billed" ? (
        <p className="notice page-note">すべての明細を請求済みです。請求済みの受注は変更できません。</p>
      ) : invoiced ? (
        <p className="notice page-note">
          請求書に載っている行(灰色の行)と取引先は変更できません。それ以外の行は、納品日の入力や変更ができます。
        </p>
      ) : null}

      <div className="stack">
        <section className="panel">
          <div className="field-grid">
            <CustomerSelect
              customers={customers}
              value={customerId}
              invalid={showErrors && !customerId}
              disabled={readOnly || invoiced}
              onChange={(v) => change(() => setCustomerId(v))}
            />
            <label>
              件名
              <input value={title} disabled={readOnly} onChange={(e) => change(() => setTitle(e.target.value))} />
            </label>
            <label>
              受注日
              <input
                type="date"
                value={orderDate}
                aria-invalid={showErrors && !orderDate}
                disabled={readOnly}
                onChange={(e) => change(() => setOrderDate(e.target.value))}
              />
            </label>
            <label>
              納期
              <input type="date" value={dueDate} disabled={readOnly} onChange={(e) => change(() => setDueDate(e.target.value))} />
            </label>
          </div>
        </section>

        <section className="panel lines-panel">
          <h2>明細</h2>
          <LineTable
            rows={rows}
            onChange={(u) => change(() => setRows(u))}
            amountByKey={amountByKey}
            showErrors={showErrors}
            withDeliveryDate
            readOnly={readOnly}
            showTax={!taxExempt}
          />
          {!readOnly && <p className="hint">納品日は請求書の取引日になります。</p>}
        </section>

        <div className="bottom-row">
          <div className="stack">
            <section className="panel">
              <label>
                備考(注文請書に印字されます)
                <textarea rows={3} value={remarks} disabled={readOnly} onChange={(e) => change(() => setRemarks(e.target.value))} />
              </label>
            </section>

            {order && (
              <section className="panel">
                <h2>請求書</h2>
                {order.invoices.length === 0 ? (
                  <p className="hint">まだ請求書は作っていません。</p>
                ) : (
                  <ul className="history">
                    {order.invoices.map((i) => (
                      <li key={i.id}>
                        <span>
                          {i.invoice_number ?? "(未発行)"}
                          {i.version > 1 && <small>第{i.version}版</small>}
                          <small>{INVOICE_STATUS[i.status] ?? i.status}</small>
                        </span>
                        <a href={`#/invoices/${i.id}`}>開く</a>
                      </li>
                    ))}
                  </ul>
                )}
                {order.unbilled_line_count > 0 && order.invoices.length > 0 && (
                  <p className="hint">まだ請求していない明細が{order.unbilled_line_count}行あります。</p>
                )}
              </section>
            )}

            {order && (
              <section className="panel">
                <h2>注文請書</h2>
                {acknowledgments.length === 0 ? (
                  <p className="hint">まだ発行していません。発行すると、その時点の内容で控えが保存されます。</p>
                ) : (
                  <ul className="history">
                    {[...acknowledgments].reverse().map((a) => (
                      <li key={a.version}>
                        <span>
                          第{a.version}版 <small>{jdatetime(a.issued_at)} 発行</small>
                        </span>
                        <button type="button" className="link-button" onClick={() => openAcknowledgment(a.version)}>
                          控えを開く
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}
          </div>

          <div className="panel totals">
            <h2>合計</h2>
            <TotalsList
              summaries={result?.tax_summaries ?? []}
              subtotal={result?.subtotal_amount ?? 0}
              tax={result?.tax_amount ?? 0}
              total={result?.total_amount ?? 0}
              exempt={taxExempt}
            />

            <div className="actions">
              {!readOnly && (
                <button type="button" onClick={handleSave} disabled={busy !== ""}>
                  {busy === "save" ? "保存しています…" : order ? "変更を保存" : "受注を登録"}
                </button>
              )}
              {order && order.unbilled_line_count > 0 && !dirty && (
                <button type="button" className={readOnly ? "" : "secondary"} onClick={handleInvoice} disabled={busy !== ""}>
                  {busy === "invoice" ? "作成しています…" : "請求書を作成"}
                </button>
              )}
              <button type="button" className="secondary" onClick={handlePreview} disabled={busy !== ""}>
                {busy === "preview" ? "PDFを作成しています…" : "注文請書をプレビュー"}
              </button>
              <button type="button" className="secondary" onClick={handleAcknowledge} disabled={busy !== ""}>
                {busy === "acknowledge" ? "発行しています…" : "注文請書を発行"}
              </button>
              {order && !readOnly && !invoiced && acknowledgments.length === 0 && (
                <button type="button" className="danger-text" onClick={handleDelete} disabled={busy !== ""}>
                  受注を削除
                </button>
              )}
            </div>

            {message && (
              <p className={message.kind === "done" ? "notice" : "alert"} role="status">
                {message.text}
              </p>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
