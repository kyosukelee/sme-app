import { useEffect, useState, type FormEvent } from "react";
import { api, type Customer, type Invoice, type InvoiceInput } from "../api";
import LineTable from "../components/LineTable";
import TotalsList from "../components/TotalsList";
import { decimal, jdate, TAX_OPTIONS, todayIso, yen } from "../format";
import { blankRow, calcFromDocument, findLineProblem, isBlank, rowsFromDocument, toLine, type Row, type SavedLine } from "../lines";
import { navigate } from "../router";
import { useCompany } from "../company";
import { useLiveCalc } from "../useLiveCalc";
import { InvoiceStatusBadge } from "./InvoiceList";
import { CustomerSelect } from "./QuoteEditor";

/** 請求書1件の画面。作成中なら編集画面、発行後は詳細画面。id がなければ新規作成 */
export default function InvoicePage({ tenantId, id }: { tenantId: string; id?: string }) {
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    setInvoice(null);
    setError("");
    api.invoice(tenantId, id).then(setInvoice).catch((e: Error) => setError(e.message));
  }, [tenantId, id]);

  if (error) {
    return (
      <>
        <a className="back" href="#/invoices">
          請求の一覧
        </a>
        <p className="alert" role="alert">
          {error}
        </p>
      </>
    );
  }
  if (id && !invoice) return <p className="placeholder">読み込み中…</p>;
  if (!invoice || invoice.status === "draft") {
    return <InvoiceEditor key={invoice?.id ?? "new"} tenantId={tenantId} initial={invoice ?? undefined} onIssued={setInvoice} />;
  }
  return <InvoiceDetail tenantId={tenantId} invoice={invoice} onChange={setInvoice} />;
}

// ---------------------------------------------------------------------
// 作成中の請求書
// ---------------------------------------------------------------------
type Busy = "" | "save" | "issue" | "preview" | "delete";

/**
 * 受注から作った行は、数量・単価・税率を請求書側で変えられない(受注は行単位で請求済みを管理するため)。
 * 訂正版は誤りを直すためのものなので、固定しない
 */
const lockFor = (version: number) => (line: SavedLine) => (version === 1 && line.order_line_id ? "amount" : "none");

function InvoiceEditor({ tenantId, initial, onIssued }: { tenantId: string; initial?: Invoice; onIssued: (i: Invoice) => void }) {
  const [invoiceId, setInvoiceId] = useState(initial?.id ?? null);
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [customerId, setCustomerId] = useState(initial?.customer_id ?? "");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [remarks, setRemarks] = useState(initial?.remarks ?? "");
  const [dueDate, setDueDate] = useState(initial?.due_date ?? "");
  const [rows, setRows] = useState<Row[]>(() =>
    initial?.lines.length ? rowsFromDocument(initial, lockFor(initial.version)) : [{ ...blankRow(), delivered_on: todayIso() }],
  );
  const [autoDueDate, setAutoDueDate] = useState(initial?.default_due_date ?? null);
  const [carry, setCarry] = useState<Pick<Invoice, "closing_date" | "carried_over_amount" | "carryovers"> | null>(initial ?? null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<Busy>("");
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const { soleProprietor, taxExempt } = useCompany();
  // 源泉徴収は個人事業主だけ。新しい請求書は、取引先を選んだときにその取引先の設定を入れる
  const [withholding, setWithholding] = useState(initial?.withholding ?? false);
  const withholdingOn = soleProprietor && withholding;

  const { calc, setCalc, amountByKey } = useLiveCalc(
    tenantId,
    rows,
    null,
    initial ? calcFromDocument(initial, rows) : null,
    (text) => setMessage({ kind: "error", text }),
    withholdingOn,
  );
  const fromOrders = rows.some((r) => r.order_line_id);

  useEffect(() => {
    api.customers(tenantId, true).then(setCustomers).catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  function change(patch: () => void) {
    patch();
    setDirty(true);
    setMessage(null);
  }

  function collect(): InvoiceInput | null {
    setShowErrors(true);
    const problem = !customerId ? "取引先を選んでください。" : findLineProblem(rows, { requireDate: "取引日" });
    if (problem) {
      setMessage({ kind: "error", text: problem });
      return null;
    }
    return {
      customer_id: customerId,
      title: title.trim() || null,
      remarks: remarks.trim() || null,
      due_date: dueDate || null,
      withholding: withholdingOn,
      lines: rows
        .filter((r) => !isBlank(r))
        .map((r) => ({ ...toLine(r), order_line_id: r.order_line_id, transaction_date: r.delivered_on, date_confirmed: r.date_confirmed })),
    };
  }

  async function save(input: InvoiceInput): Promise<Invoice> {
    const saved = invoiceId ? await api.updateInvoice(tenantId, invoiceId, input) : await api.createInvoice(tenantId, input);
    const savedRows = rowsFromDocument(saved, lockFor(saved.version));
    setRows(savedRows);
    setCalc(calcFromDocument(saved, savedRows));
    setTitle(saved.title ?? "");
    setRemarks(saved.remarks ?? "");
    setDueDate(saved.due_date ?? "");
    setAutoDueDate(saved.default_due_date);
    setCarry(saved);
    setWithholding(saved.withholding);
    setInvoiceId(saved.id);
    setDirty(false);
    return saved;
  }

  async function ensureSaved(): Promise<string | null> {
    if (invoiceId && !dirty) return invoiceId;
    const input = collect();
    return input ? (await save(input)).id : null;
  }

  async function handleSave() {
    const input = collect();
    if (!input) return;
    setBusy("save");
    try {
      const saved = await save(input);
      if (!initial) {
        navigate(`/invoices/${saved.id}`);
        return;
      }
      setMessage({ kind: "done", text: "下書きを保存しました" });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function handlePreview() {
    if (!collect()) return;
    const tab = window.open("", "_blank");
    setBusy("preview");
    try {
      const id = await ensureSaved();
      if (!id) throw new Error("入力内容を確認してください。");
      await api.openInvoicePdf(tenantId, id, tab);
    } catch (e) {
      tab?.close();
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function handleIssue() {
    if (!collect()) return;
    const what = initial && initial.version > 1 ? `訂正版(第${initial.version}版)を発行すると、旧版は「訂正済」になります。` : "発行すると請求番号が付き、内容は変更できなくなります。";
    if (!window.confirm(`${what}発行しますか？`)) return;
    setBusy("issue");
    try {
      const id = await ensureSaved();
      if (!id) throw new Error("入力内容を確認してください。");
      const issued = await api.issueInvoice(tenantId, id);
      if (initial) onIssued(issued);
      else navigate(`/invoices/${issued.id}`);
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
      setBusy("");
    }
  }

  async function handleDelete() {
    if (!invoiceId || !window.confirm("この下書きを削除しますか？受注から作った明細は、また請求できる状態に戻ります。")) return;
    setBusy("delete");
    try {
      await api.deleteInvoice(tenantId, invoiceId);
      navigate(initial?.previous_version_id ? `/invoices/${initial.previous_version_id}` : "/invoices");
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
      setBusy("");
    }
  }

  const result = calc?.result;
  const isCorrection = !!initial && initial.version > 1;

  return (
    <>
      <div className="page-head">
        <a className="back" href="#/invoices">
          請求の一覧
        </a>
        <h1>{isCorrection ? `${initial!.invoice_number} の訂正版(第${initial!.version}版)` : initial ? "請求書の下書き" : "請求書を作成"}</h1>
        {dirty && <p className="count">未保存の変更があります</p>}
      </div>

      {isCorrection && (
        <p className="notice page-note">
          発行済みの請求書を訂正する下書きです。発行すると、<a href={`#/invoices/${initial!.previous_version_id}`}>元の版</a>
          は「訂正済」になり、登録済みの入金はこの版に引き継がれます。
        </p>
      )}

      <div className="stack">
        <section className="panel">
          <div className="field-grid">
            <CustomerSelect
              customers={customers}
              value={customerId}
              invalid={showErrors && !customerId}
              disabled={fromOrders}
              onChange={(v) =>
                change(() => {
                  setCustomerId(v);
                  if (!initial) setWithholding(customers?.find((c) => c.id === v)?.withholding_default ?? false);
                })
              }
            />
            <label>
              件名
              <input value={title} onChange={(e) => change(() => setTitle(e.target.value))} />
            </label>
            {soleProprietor && (
              <label className="check withholding-check">
                <input type="checkbox" checked={withholding} onChange={(e) => change(() => setWithholding(e.target.checked))} />
                源泉徴収税額を差し引く
                <span className="hint">税抜金額の10.21%(100万円を超える部分は20.42%)</span>
              </label>
            )}
            <label>
              お支払期限
              <input type="date" value={dueDate} onChange={(e) => change(() => setDueDate(e.target.value))} />
              {!dueDate && (
                <span className="hint">
                  空欄なら発行時に締め日・支払日から決めます
                  {autoDueDate && !dirty && `(${jdate(autoDueDate)})`}
                </span>
              )}
            </label>
          </div>
          {initial && initial.orders.length > 0 && (
            <p className="hint">
              元の受注:{" "}
              {initial.orders.map((o, i) => (
                <span key={o.id}>
                  {i > 0 && "、"}
                  <a href={`#/orders/${o.id}`}>{o.order_number}</a>
                </span>
              ))}
              (受注から作った明細があるため、取引先は変更できません)
            </p>
          )}
        </section>

        <section className="panel lines-panel">
          <h2>明細</h2>
          <LineTable
            rows={rows}
            onChange={(u) => change(() => setRows(u))}
            amountByKey={amountByKey}
            showErrors={showErrors}
            withDeliveryDate
            dateLabel="取引日"
            dateRequired
            showTax={!taxExempt}
          />
          {rows.some((r) => !r.date_confirmed) && (
            <div className="notice-warn provisional-note">
              <p>
                {rows
                  .map((r, i) => (r.date_confirmed ? null : `${i + 1}行目`))
                  .filter(Boolean)
                  .join("・")}
                の取引日は仮の日付です。受注に納品日が入っていなかったため、納期または受注日を入れています。
                正しい取引日に直すか、この日付で正しければ確定してください。確定するまで発行できません。
              </p>
              <button
                type="button"
                className="secondary"
                onClick={() => change(() => setRows((list) => list.map((r) => ({ ...r, date_confirmed: true }))))}
              >
                この日付で確定
              </button>
            </div>
          )}
          <p className="hint">
            取引日は請求書に印字されます(インボイスの記載事項)。
            {rows.some((r) => r.lock === "amount") &&
              "受注から作った行(灰色の欄)の数量・単価・税率は、ここでは変更できません。変更が必要なときは、行を×で外して受注を直してください。"}
          </p>
        </section>

        <div className="bottom-row">
          <section className="panel">
            <label>
              備考(請求書に印字されます)
              <textarea rows={3} value={remarks} onChange={(e) => change(() => setRemarks(e.target.value))} />
            </label>
          </section>

          <div className="panel totals">
            <h2>合計</h2>
            <TotalsList
              summaries={result?.tax_summaries ?? []}
              subtotal={result?.subtotal_amount ?? 0}
              tax={result?.tax_amount ?? 0}
              total={result?.total_amount ?? 0}
              exempt={taxExempt}
              withholding={
                withholdingOn ? { amount: result?.withholding_amount ?? 0, net: result?.net_amount ?? result?.total_amount ?? 0 } : null
              }
            />
            {carry && (
              <Carryover
                invoice={{ ...carry, net_amount: String(result?.net_amount ?? result?.total_amount ?? 0) }}
                draft
              />
            )}
            <div className="actions">
              <button type="button" onClick={handleIssue} disabled={busy !== ""}>
                {busy === "issue" ? "発行しています…" : isCorrection ? "訂正版を発行する" : "発行する"}
              </button>
              <button type="button" className="secondary" onClick={handleSave} disabled={busy !== ""}>
                {busy === "save" ? "保存しています…" : "下書きを保存"}
              </button>
              <button type="button" className="secondary" onClick={handlePreview} disabled={busy !== ""}>
                {busy === "preview" ? "PDFを作成しています…" : "請求書をプレビュー"}
              </button>
              {invoiceId && (
                <button type="button" className="danger-text" onClick={handleDelete} disabled={busy !== ""}>
                  下書きを削除
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

// ---------------------------------------------------------------------
// 発行後の請求書
// ---------------------------------------------------------------------
const TAX_LABEL = Object.fromEntries(TAX_OPTIONS.map((o) => [o.value, o.label]));

function InvoiceDetail({ tenantId, invoice, onChange }: { tenantId: string; invoice: Invoice; onChange: (i: Invoice) => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const latest = invoice.superseded_at === null;
  const latestVersion = invoice.versions[invoice.versions.length - 1];

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const openPdf = (id: string) => run(() => api.openInvoicePdf(tenantId, id));

  const correct = () =>
    run(async () => {
      if (invoice.correction_draft_id) {
        navigate(`/invoices/${invoice.correction_draft_id}`);
        return;
      }
      if (!window.confirm("この請求書の訂正版(下書き)を作りますか？発行するまで、この版はそのまま有効です。")) return;
      const draft = await api.correctInvoice(tenantId, invoice.id);
      navigate(`/invoices/${draft.id}`);
    });

  const voidInvoice = () =>
    run(async () => {
      if (!window.confirm("この請求書を取り消しますか？元の受注は「未請求」に戻ります。取り消した請求書は元に戻せません。")) return;
      onChange(await api.voidInvoice(tenantId, invoice.id));
    });

  return (
    <>
      <div className="page-head">
        <a className="back" href="#/invoices">
          請求の一覧
        </a>
        <h1>
          {invoice.invoice_number}
          {invoice.version > 1 && <span className="version">第{invoice.version}版</span>}
        </h1>
        {latest ? <InvoiceStatusBadge invoice={invoice} /> : <span className="badge is-lost">訂正済</span>}
      </div>

      {!latest && latestVersion && (
        <p className="notice-warn page-note">
          この版は訂正済みです。最新は<a href={`#/invoices/${latestVersion.id}`}>第{latestVersion.version}版</a>です。
        </p>
      )}

      <div className="editor">
        <div className="editor-main">
          <section className="panel">
            <dl className="facts">
              <div>
                <dt>取引先</dt>
                <dd>
                  {invoice.customer_name} {invoice.customer_honorific}
                </dd>
              </div>
              <div>
                <dt>件名</dt>
                <dd>{invoice.title ?? "―"}</dd>
              </div>
              <div>
                <dt>請求日</dt>
                <dd>{jdate(invoice.issue_date)}</dd>
              </div>
              <div>
                <dt>お支払期限</dt>
                <dd>{jdate(invoice.due_date)}</dd>
              </div>
              <div>
                <dt>発行者</dt>
                <dd>
                  {invoice.issuer_name}
                  {invoice.issuer_representative && ` ${invoice.issuer_representative}`}
                  <br />
                  <small>
                    {invoice.issuer_registration_number
                      ? `登録番号 ${invoice.issuer_registration_number}`
                      : "登録番号なし(適格請求書ではありません)"}
                  </small>
                </dd>
              </div>
              {invoice.orders.length > 0 && (
                <div>
                  <dt>元の受注</dt>
                  <dd>
                    {invoice.orders.map((o, i) => (
                      <span key={o.id}>
                        {i > 0 && "、"}
                        <a href={`#/orders/${o.id}`}>{o.order_number}</a>
                      </span>
                    ))}
                  </dd>
                </div>
              )}
            </dl>
          </section>

          <section className="panel lines-panel">
            <h2>明細</h2>
            <div className="table-wrap flat">
              <table className="lines readonly">
                <thead>
                  <tr>
                    <th scope="col">取引日</th>
                    <th scope="col">品名</th>
                    <th scope="col" className="num">
                      数量
                    </th>
                    <th scope="col">単位</th>
                    <th scope="col" className="num">
                      単価(税抜)
                    </th>
                    {!invoice.issuer_tax_exempt && <th scope="col">税率</th>}
                    <th scope="col" className="num">
                      金額
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {invoice.lines.map((l) => (
                    <tr key={l.line_id}>
                      <td>{jdate(l.transaction_date)}</td>
                      <td>{l.description}</td>
                      <td className="num">{decimal(l.quantity)}</td>
                      <td>{l.unit ?? ""}</td>
                      <td className="num">{decimal(l.unit_price)}</td>
                      {!invoice.issuer_tax_exempt && <td>{TAX_LABEL[l.tax_category]}</td>}
                      <td className="num">{yen(l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {latest && invoice.status !== "void" && (
            <Payments tenantId={tenantId} invoice={invoice} onChange={onChange} />
          )}

          {invoice.versions.length > 1 && (
            <section className="panel">
              <h2>版の履歴</h2>
              <ul className="history">
                {invoice.versions.map((v) => (
                  <li key={v.id}>
                    <span>
                      第{v.version}版 <small>{v.issue_date ? `${jdate(v.issue_date)} 発行` : "作成中"}</small>
                      {v.superseded_at && <small>(訂正済)</small>}
                    </span>
                    {v.id === invoice.id ? (
                      <small>この版</small>
                    ) : (
                      <a href={`#/invoices/${v.id}`}>開く</a>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <aside className="editor-side">
          <div className="panel totals">
            <h2>合計</h2>
            <TotalsList
              summaries={invoice.tax_summaries}
              subtotal={invoice.subtotal_amount}
              tax={invoice.tax_amount}
              total={invoice.total_amount}
              exempt={invoice.status === "draft" ? false : invoice.issuer_tax_exempt}
              withholding={invoice.withholding ? { amount: invoice.withholding_amount, net: invoice.net_amount } : null}
            />
            <Carryover invoice={invoice} />
            {invoice.status !== "void" && latest && (
              <dl className="balance">
                <div>
                  <dt>入金済み</dt>
                  <dd>{yen(invoice.paid_amount)}</dd>
                </div>
                {Number(invoice.balance) < 0 ? (
                  <div className="overpaid">
                    <dt>過入金</dt>
                    <dd>{yen(-Number(invoice.balance))}</dd>
                  </div>
                ) : (
                  <div>
                    <dt>残額</dt>
                    <dd>{yen(invoice.balance)}</dd>
                  </div>
                )}
              </dl>
            )}

            <div className="actions">
              <button type="button" onClick={() => openPdf(invoice.id)} disabled={busy}>
                請求書PDFを開く
              </button>
              {latest && (invoice.status === "issued" || invoice.status === "paid") && (
                <button type="button" className="secondary" onClick={correct} disabled={busy}>
                  {invoice.correction_draft_id ? "作成中の訂正版を開く" : "訂正版を作成"}
                </button>
              )}
              {latest && invoice.status === "issued" && invoice.payments.length === 0 && !invoice.correction_draft_id && (
                <button type="button" className="danger-text" onClick={voidInvoice} disabled={busy}>
                  請求書を取り消す
                </button>
              )}
            </div>
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

function Payments({ tenantId, invoice, onChange }: { tenantId: string; invoice: Invoice; onChange: (i: Invoice) => void }) {
  const [paidOn, setPaidOn] = useState(todayIso());
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);
  const balance = Number(invoice.balance);
  const overpaid = balance < 0; // 訂正で減額したときなど、入金が請求額を上回っている
  const open = Math.abs(balance); // 残額、または過入金の額
  const word = overpaid ? "返金" : "入金";

  async function submit(e: FormEvent) {
    e.preventDefault();
    const value = amount.replace(/[,，\s]/g, "").replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
    if (!/^\d+$/.test(value) || Number(value) <= 0) {
      setMessage({ kind: "error", text: `${word}額を円単位の数字で入力してください。` });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      // 返金はマイナスの入金として記録する
      const signed = overpaid ? `-${value}` : value;
      onChange(await api.addPayment(tenantId, invoice.id, { paid_on: paidOn, amount: signed, memo: memo.trim() || null }));
      setAmount("");
      setMemo("");
      setMessage({ kind: "done", text: `${yen(value)}の${word}を登録しました` });
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function remove(paymentId: string, value: string) {
    const label = Number(value) < 0 ? `${yen(-Number(value))}の返金` : `${yen(value)}の入金`;
    if (!window.confirm(`${label}を取り消しますか？`)) return;
    setBusy(true);
    try {
      onChange(await api.deletePayment(tenantId, invoice.id, paymentId));
      setMessage(null);
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>入金</h2>
      {invoice.payments.length === 0 ? (
        <p className="hint">まだ入金はありません。</p>
      ) : (
        <ul className="history">
          {invoice.payments.map((p) => (
            <li key={p.id}>
              <span>
                {jdate(p.paid_on)}{" "}
                {Number(p.amount) < 0 ? <strong>返金 {yen(-Number(p.amount))}</strong> : <strong>{yen(p.amount)}</strong>}
                {p.memo && <small>{p.memo}</small>}
              </span>
              {p.receipt_id ? (
                <a href={`#/receipts?customer=${invoice.customer_id}`}>入金を見る</a>
              ) : (
                <button type="button" className="link-button" onClick={() => remove(p.id, p.amount)} disabled={busy}>
                  取り消す
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {Number(invoice.carried_over_amount) > 0 && !overpaid && (
        <p className="hint">
          繰越分も含めてまとめて振り込まれたときは、<a href={`#/receipts?customer=${invoice.customer_id}`}>入金の画面</a>
          で登録すると、古い請求書から順に割り当てます。
        </p>
      )}

      {overpaid && (
        <p className="notice-warn">
          入金が請求額を{yen(open)}上回っています(過入金)。お客様に返金したら、下の欄で返金を登録してください。
        </p>
      )}

      {open > 0 && (
        <form className="payment-form" onSubmit={submit}>
          <label>
            {word}日
            <input type="date" required value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
          </label>
          <label>
            {word}額(円)
            <input className="num" inputMode="numeric" value={amount} placeholder={String(open)} onChange={(e) => setAmount(e.target.value)} />
          </label>
          <label>
            メモ
            <input
              value={memo}
              placeholder={overpaid ? "例: 訂正による差額を振込で返金" : "例: 振込手数料を差し引いて入金"}
              onChange={(e) => setMemo(e.target.value)}
            />
          </label>
          <div className="payment-buttons">
            <button type="button" className="secondary" onClick={() => setAmount(String(open))} disabled={busy}>
              {overpaid ? "過入金の額を入れる" : "残額を入れる"}
            </button>
            <button type="submit" disabled={busy}>
              {busy ? "登録しています…" : `${word}を登録`}
            </button>
          </div>
        </form>
      )}
      {message && (
        <p className={message.kind === "done" ? "notice" : "alert"} role="status">
          {message.text}
        </p>
      )}
    </section>
  );
}

/**
 * 締め請求の繰越(前回までの未入金)。繰越は金額のお知らせで、この請求書の金額(インボイス)には含めない。
 * 作成中は今の時点の見込み、発行後は発行時に確定した金額を出す
 */
function Carryover({
  invoice,
  draft = false,
}: {
  invoice: Pick<Invoice, "closing_date" | "carried_over_amount" | "carryovers" | "net_amount">;
  draft?: boolean;
}) {
  if (!invoice.closing_date || Number(invoice.carried_over_amount) <= 0) return null;
  // 今回ご請求額 = 繰越 + 差引ご請求額(源泉徴収がなければ合計と同じ)
  const due = Number(invoice.carried_over_amount) + Number(invoice.net_amount);
  return (
    <div className="carryover">
      <dl>
        <div>
          <dt>前回までの未入金{draft && "(見込み)"}</dt>
          <dd>{yen(invoice.carried_over_amount)}</dd>
        </div>
        <div className="due">
          <dt>今回ご請求額</dt>
          <dd>{yen(due)}</dd>
        </div>
      </dl>
      <details>
        <summary>繰越の内訳({invoice.carryovers.length}件)</summary>
        <ul>
          {invoice.carryovers.map((c) => (
            <li key={c.source_invoice_id}>
              <a href={`#/invoices/${c.source_invoice_id}`}>
                {c.source_number}
                {c.source_version > 1 && ` 第${c.source_version}版`}
              </a>
              <span>{yen(c.balance)}</span>
            </li>
          ))}
        </ul>
      </details>
      <p className="hint">
        {draft
          ? "発行する時点の未入金で確定し、請求書に「今回ご請求額」として印字します。"
          : "発行時点の未入金です。請求書の金額(インボイス)には含めていません。"}
      </p>
    </div>
  );
}
