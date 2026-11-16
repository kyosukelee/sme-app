import { useEffect, useState } from "react";
import { api, type Customer, type Quote, type QuoteInput } from "../api";
import LineTable from "../components/LineTable";
import TotalsList from "../components/TotalsList";
import { blankRow, calcFromDocument, findLineProblem, isBlank, rowsFromDocument, toLine, type Row } from "../lines";
import { navigate } from "../router";
import { useCompany } from "../company";
import { useLiveCalc } from "../useLiveCalc";

type Props = {
  tenantId: string;
  quote?: Quote; // 作成中の見積を編集するとき。新規作成なら undefined
  onIssued: (quote: Quote) => void;
};

export default function QuoteEditor({ tenantId, quote, onIssued }: Props) {
  const { taxExempt } = useCompany();
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [quoteId, setQuoteId] = useState(quote?.id ?? null);
  const [customerId, setCustomerId] = useState(quote?.customer_id ?? "");
  const [title, setTitle] = useState(quote?.title ?? "");
  const [remarks, setRemarks] = useState(quote?.remarks ?? "");
  const [rows, setRows] = useState<Row[]>(() => (quote?.lines.length ? rowsFromDocument(quote) : [blankRow(), blankRow()]));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "issue" | "preview" | "delete">("");
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  // 保存済みの下書きを開いたときは、計算が返るまで保存されている金額を表示する
  const { calc, setCalc, amountByKey } = useLiveCalc(tenantId, rows, null, quote ? calcFromDocument(quote, rows) : null, (text) =>
    setMessage({ kind: "error", text }),
  );

  useEffect(() => {
    api.customers(tenantId, true).then(setCustomers).catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  function change(patch: () => void) {
    patch();
    setDirty(true);
    setMessage(null);
  }

  /** 入力内容を確かめて保存用の形にする。問題があれば理由を表示して null を返す */
  function collect(): QuoteInput | null {
    setShowErrors(true);
    const problem = !customerId ? "取引先を選んでください。" : findLineProblem(rows);
    if (problem) {
      setMessage({ kind: "error", text: problem });
      return null;
    }
    return {
      customer_id: customerId,
      title: title.trim() || null,
      remarks: remarks.trim() || null,
      lines: rows.filter((r) => !isBlank(r)).map(toLine),
    };
  }

  /** 保存して、保存後の見積を返す。新規ならここで作成する */
  async function save(input: QuoteInput): Promise<Quote> {
    const saved = quoteId ? await api.updateQuote(tenantId, quoteId, input) : await api.createQuote(tenantId, input);
    // 保存後はサーバーが整えた内容(全角英数字の半角化など)を画面に反映する
    const savedRows = rowsFromDocument(saved);
    setRows(savedRows);
    setCalc(calcFromDocument(saved, savedRows));
    setTitle(saved.title ?? "");
    setRemarks(saved.remarks ?? "");
    setQuoteId(saved.id);
    setDirty(false);
    return saved;
  }

  async function handleSave() {
    const input = collect();
    if (!input) return;
    setBusy("save");
    try {
      const saved = await save(input);
      if (!quote) {
        navigate(`/quotes/${saved.id}`);
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
    const input = collect();
    if (!input) return;
    const tab = window.open("", "_blank"); // ポップアップがブロックされないよう、クリック直後に開く
    setBusy("preview");
    try {
      const saved = dirty || !quoteId ? await save(input) : { id: quoteId };
      await api.openQuotePdf(tenantId, saved.id, tab);
    } catch (e) {
      tab?.close();
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function handleIssue() {
    const input = collect();
    if (!input) return;
    if (!window.confirm("発行すると見積番号が付き、内容は変更できなくなります。発行しますか？")) return;
    setBusy("issue");
    try {
      const saved = dirty || !quoteId ? await save(input) : { id: quoteId };
      const issued = await api.issueQuote(tenantId, saved.id);
      if (quote) onIssued(issued);
      else navigate(`/quotes/${issued.id}`);
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
      setBusy("");
    }
  }

  async function handleDelete() {
    if (!quoteId || !window.confirm("この下書きを削除しますか？")) return;
    setBusy("delete");
    try {
      await api.deleteQuote(tenantId, quoteId);
      navigate("/quotes");
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
      setBusy("");
    }
  }

  const result = calc?.result;

  return (
    <>
      <div className="page-head">
        <a className="back" href="#/quotes">
          見積の一覧
        </a>
        <h1>{quote ? "見積の下書き" : "見積を作成"}</h1>
        {dirty && <p className="count">未保存の変更があります</p>}
      </div>

      <div className="editor">
        <div className="editor-main">
          <section className="panel">
            <div className="field-row">
              <CustomerSelect
                customers={customers}
                value={customerId}
                invalid={showErrors && !customerId}
                onChange={(id) => change(() => setCustomerId(id))}
              />
              <label>
                件名
                <input value={title} onChange={(e) => change(() => setTitle(e.target.value))} placeholder="例: ホームページ制作一式" />
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
              showTax={!taxExempt}
            />
          </section>

          <section className="panel">
            <label>
              備考(見積書に印字されます)
              <textarea
                rows={3}
                value={remarks}
                onChange={(e) => change(() => setRemarks(e.target.value))}
                placeholder="例: お支払い条件:月末締め翌月末払い"
              />
            </label>
          </section>
        </div>

        <aside className="editor-side">
          <div className="panel totals">
            <h2>合計</h2>
            <TotalsList
              summaries={result?.tax_summaries ?? []}
              subtotal={result?.subtotal_amount ?? 0}
              tax={result?.tax_amount ?? 0}
              total={result?.total_amount ?? 0}
              exempt={taxExempt}
            />
            <p className="hint">消費税は税率ごとに合計してから端数を処理しています。</p>

            <div className="actions">
              <button type="button" onClick={handleIssue} disabled={busy !== ""}>
                {busy === "issue" ? "発行しています…" : "発行する"}
              </button>
              <button type="button" className="secondary" onClick={handleSave} disabled={busy !== ""}>
                {busy === "save" ? "保存しています…" : "下書きを保存"}
              </button>
              <button type="button" className="secondary" onClick={handlePreview} disabled={busy !== ""}>
                {busy === "preview" ? "PDFを作成しています…" : "見積書をプレビュー"}
              </button>
              {quoteId && (
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
        </aside>
      </div>
    </>
  );
}

/**
 * 取引先の選択欄。取引先がまだなければ登録画面へ案内する。
 * 使用中止の取引先は、選んである場合(作成中の書類の取引先)と includeInactive のときだけ出す
 */
export function CustomerSelect({
  customers,
  value,
  invalid,
  disabled = false,
  includeInactive = false,
  onChange,
}: {
  customers: Customer[] | null;
  value: string;
  invalid: boolean;
  disabled?: boolean;
  includeInactive?: boolean;
  onChange: (id: string) => void;
}) {
  const options = customers?.filter((c) => c.is_active || includeInactive || c.id === value);
  return (
    <label>
      取引先
      {options && options.length === 0 ? (
        <span className="hint">
          取引先がまだありません。<a href="#/customers">取引先を追加</a>してください。
        </span>
      ) : (
        <select value={value} aria-invalid={invalid} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          <option value="">選んでください</option>
          {options?.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} {c.honorific}
              {c.is_active ? "" : "(使用中止)"}
            </option>
          ))}
        </select>
      )}
    </label>
  );
}
