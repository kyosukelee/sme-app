import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type BankFormat,
  type BankFormatInput,
  type BankImportSummary,
  type BankPreview,
  type BankStatement,
  type Customer,
  type Encoding,
  type MatchCandidate,
  type OpenInvoice,
  type StatementStatus,
  type StatementsResponse,
} from "../api";
import { jdate, yen } from "../format";
import { CustomerSelect } from "./QuoteEditor";

type Message = { kind: "done" | "error"; text: string } | null;
type BankFile = { filename: string; content_base64: string };

const TABS: { value: StatementStatus; label: string }[] = [
  { value: "open", label: "未消込" },
  { value: "matched", label: "消込済" },
  { value: "ignored", label: "対象外" },
];

/**
 * 入金消込。銀行の入出金明細(CSV)を取り込み、入金ごとに消し込む請求書の候補を出す。
 * 候補を確かめて「確定」すると、入金を登録して請求書に割り当てる
 */
export default function Reconcile({ tenantId }: { tenantId: string }) {
  const [tab, setTab] = useState<StatementStatus>("open");
  const [data, setData] = useState<StatementsResponse | null>(null);
  const [formats, setFormats] = useState<BankFormat[] | null>(null);
  const [imports, setImports] = useState<BankImportSummary[]>([]);
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [message, setMessage] = useState<Message>(null);

  const loadStatements = useCallback(async () => {
    setData(await api.bankStatements(tenantId, tab));
  }, [tenantId, tab]);

  const loadSetup = useCallback(async () => {
    const [f, i] = await Promise.all([api.bankFormats(tenantId), api.bankImports(tenantId)]);
    setFormats(f);
    setImports(i);
  }, [tenantId]);

  useEffect(() => {
    setData(null);
    loadStatements().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [loadStatements]);

  useEffect(() => {
    setFormats(null);
    loadSetup().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
    api.customers(tenantId, true).then(setCustomers).catch(() => setCustomers([]));
  }, [tenantId, loadSetup]);

  const refresh = () => {
    loadStatements().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
    loadSetup().catch(() => {});
  };

  return (
    <>
      <div className="page-head">
        <h1>入金消込</h1>
        {data && <p className="count">未消込 {data.counts.open}件</p>}
      </div>
      <p className="lead">
        銀行の入出金明細(CSV)を取り込むと、入金ごとに消し込む請求書の候補が出ます。内容を確かめて「確定」すると、入金を登録します。
      </p>

      {message && (
        <p className={message.kind === "done" ? "notice" : "alert"} role="status">
          {message.text}
        </p>
      )}

      <ImportPanel
        tenantId={tenantId}
        formats={formats}
        imports={imports}
        onChanged={(text) => {
          if (text) setMessage({ kind: "done", text });
          setTab("open");
          refresh();
        }}
        onError={(text) => setMessage({ kind: "error", text })}
      />

      <div className="tabs" role="tablist" aria-label="明細の状態">
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={tab === t.value}
            className="tab"
            onClick={() => setTab(t.value)}
          >
            {t.label}
            {data && <span className="tab-count">{data.counts[t.value]}</span>}
          </button>
        ))}
      </div>

      {data === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : data.statements.length === 0 ? (
        <p className="placeholder">
          {tab === "open"
            ? "未消込の入金はありません。銀行の入出金明細を取り込んでください。"
            : tab === "matched"
              ? "消込を確定した入金はまだありません。"
              : "対象外にした入金はありません。"}
        </p>
      ) : tab === "open" ? (
        <div className="statement-list">
          {data.statements.map((s) => (
            <StatementCard
              key={s.id}
              tenantId={tenantId}
              statement={s}
              feeLimit={Number(data.fee_limit)}
              customers={customers}
              onDone={(text) => {
                setMessage({ kind: "done", text });
                refresh();
              }}
            />
          ))}
        </div>
      ) : (
        <ProcessedTable
          tenantId={tenantId}
          statements={data.statements}
          kind={tab}
          onDone={(text) => {
            setMessage({ kind: "done", text });
            refresh();
          }}
          onError={(text) => setMessage({ kind: "error", text })}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------
// 取り込み
// ---------------------------------------------------------------------

async function readFile(file: File): Promise<BankFile> {
  if (file.size > 2 * 1024 * 1024) throw new Error("ファイルが大きすぎます(2MBまで)。期間を分けて取り込んでください");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return { filename: file.name, content_base64: btoa(binary) };
}

function ImportPanel({
  tenantId,
  formats,
  imports,
  onChanged,
  onError,
}: {
  tenantId: string;
  formats: BankFormat[] | null;
  imports: BankImportSummary[];
  onChanged: (text: string | null) => void;
  onError: (text: string) => void;
}) {
  const [file, setFile] = useState<BankFile | null>(null);
  const [formatId, setFormatId] = useState("");
  const [editing, setEditing] = useState<BankFormat | "new" | null>(null);
  const [busy, setBusy] = useState(false);
  const [inputKey, setInputKey] = useState(0); // 取り込んだあとにファイルの選択を空に戻す

  useEffect(() => {
    if (formats && formats.length > 0 && !formats.some((f) => f.id === formatId)) setFormatId(formats[0].id);
  }, [formats, formatId]);

  async function choose(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0];
    if (!picked) return;
    try {
      setFile(await readFile(picked));
      if (formats && formats.length === 0) setEditing("new"); // 初めてなら、列の対応の設定から
    } catch (err) {
      onError((err as Error).message);
    }
  }

  async function runImport() {
    if (!file || !formatId) return;
    setBusy(true);
    try {
      const r = await api.importBankFile(tenantId, formatId, file);
      const parts = [`入金 ${r.imported}件を取り込みました`];
      if (r.duplicates) parts.push(`取り込み済みの${r.duplicates}件は飛ばしました`);
      if (r.skipped) parts.push(`出金など${r.skipped}行は対象外です`);
      if (r.problems.length) parts.push(`読めなかった行: ${r.problems.map((p) => `${p.row}行目(${p.message})`).join("、")}`);
      setFile(null);
      setInputKey((k) => k + 1);
      onChanged(parts.join("。"));
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancelImport(i: BankImportSummary) {
    if (!window.confirm(`${i.filename ?? "この取り込み"}で取り込んだ明細(${i.imported_count}件)を取り消しますか?`)) return;
    try {
      await api.deleteBankImport(tenantId, i.id);
      onChanged("取り込みを取り消しました");
    } catch (err) {
      onError((err as Error).message);
    }
  }

  const current = formats?.find((f) => f.id === formatId);

  return (
    <section className="panel import-panel">
      <h2>明細の取り込み</h2>
      <div className="import-row">
        <label>
          銀行のCSVファイル
          <input key={inputKey} type="file" accept=".csv,.txt,text/csv" onChange={choose} />
        </label>
        <label>
          取込形式
          {formats && formats.length === 0 ? (
            <span className="hint">まだありません。ファイルを選ぶと、列の対応を設定できます。</span>
          ) : (
            <select value={formatId} onChange={(e) => setFormatId(e.target.value)} disabled={!formats}>
              {formats?.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
        </label>
        <div className="actions inline">
          <button type="button" onClick={runImport} disabled={busy || !file || !formatId}>
            {busy ? "取り込んでいます…" : "取り込む"}
          </button>
        </div>
      </div>
      <p className="hint">
        ネットバンキングの「入出金明細」をCSVで保存して選びます。入金の行だけを取り込み、前に取り込んだ明細は自動で飛ばします。
        <button type="button" className="link-button" onClick={() => setEditing("new")}>
          取込形式を追加
        </button>
        {current && (
          <>
            {" ・ "}
            <button type="button" className="link-button" onClick={() => setEditing(current)}>
              「{current.name}」の列の対応を直す
            </button>
          </>
        )}
      </p>

      {editing && (
        <FormatEditor
          key={editing === "new" ? "new" : editing.id}
          tenantId={tenantId}
          format={editing === "new" ? null : editing}
          initialFile={file}
          onFile={setFile}
          onSaved={(f) => {
            setEditing(null);
            setFormatId(f.id);
            onChanged(`取込形式「${f.name}」を保存しました${file ? "。「取り込む」を押してください" : ""}`);
          }}
          onCancel={() => setEditing(null)}
        />
      )}

      {imports.length > 0 && (
        <details className="import-history">
          <summary>最近の取り込み({imports.length}件)</summary>
          <ul className="history">
            {imports.map((i) => (
              <li key={i.id}>
                <span>
                  {jdate(i.created_at.slice(0, 10))} {i.filename ?? "(ファイル名なし)"}
                  <small>
                    {i.format_name ?? "削除した取込形式"} ・ 入金 {i.imported_count}件
                    {i.duplicate_count > 0 && `(重複 ${i.duplicate_count}件)`} ・ 確定 {i.matched_count}件
                  </small>
                </span>
                {i.matched_count === 0 && (
                  <button type="button" className="link-button" onClick={() => cancelImport(i)}>
                    取り消す
                  </button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

const ROLE_LABELS: { key: "date_col" | "amount_col" | "payer_col" | "memo_col"; label: string; required: boolean }[] = [
  { key: "date_col", label: "取引日", required: true },
  { key: "amount_col", label: "入金額", required: true },
  { key: "payer_col", label: "振込名義", required: true },
  { key: "memo_col", label: "メモ(任意)", required: false },
];

/** 取込形式(列の対応)の設定。CSV の先頭の行を見ながら、どの列が何かを選ぶ */
function FormatEditor({
  tenantId,
  format,
  initialFile,
  onFile,
  onSaved,
  onCancel,
}: {
  tenantId: string;
  format: BankFormat | null;
  initialFile: BankFile | null;
  onFile: (f: BankFile) => void;
  onSaved: (f: BankFormat) => void;
  onCancel: () => void;
}) {
  const [file, setFile] = useState<BankFile | null>(initialFile);
  const [encoding, setEncoding] = useState<Encoding>(format?.encoding ?? "auto");
  const [preview, setPreview] = useState<BankPreview | null>(null);
  const [form, setForm] = useState<Omit<BankFormatInput, "encoding">>(
    format ?? { name: "", skip_rows: 1, date_col: -1, amount_col: -1, payer_col: -1, memo_col: null },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!file) return;
    let alive = true;
    api
      .previewBankFile(tenantId, file, encoding)
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        setError("");
        // 新しい取込形式なら、見出しから推測した列を最初に入れる
        if (!format) {
          setForm((f) => ({
            ...f,
            skip_rows: p.guess.skip_rows,
            date_col: p.guess.date_col ?? -1,
            amount_col: p.guess.amount_col ?? -1,
            payer_col: p.guess.payer_col ?? -1,
            memo_col: p.guess.memo_col,
          }));
        }
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [tenantId, file, encoding, format]);

  const columnCount = Math.max(0, ...(preview?.rows.map((r) => r.length) ?? [0]));
  // 列ごとの見本の値。出金の行では入金額が空なので、値が入っている最初の行から取る
  const dataRows = preview?.rows.slice(form.skip_rows) ?? [];
  const sampleOf = (col: number) => dataRows.find((r) => r[col])?.[col] ?? "";
  const roleOf = (col: number) => ROLE_LABELS.find((r) => form[r.key] === col)?.label.replace("(任意)", "");

  async function save() {
    if (!form.name.trim()) {
      setError("取込形式の名前を入力してください(例: 〇〇銀行 普通 1234567)");
      return;
    }
    if ([form.date_col, form.amount_col, form.payer_col].some((c) => c < 0)) {
      setError("取引日・入金額・振込名義の列を選んでください");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const input = { ...form, name: form.name.trim(), encoding };
      onSaved(format ? await api.updateBankFormat(tenantId, format.id, input) : await api.createBankFormat(tenantId, input));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="format-editor">
      <h3>{format ? `取込形式「${format.name}」の列の対応` : "取込形式を追加"}</h3>
      <div className="field-row">
        <label>
          名前
          <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="例: 〇〇銀行 普通 1234567" />
        </label>
        <label>
          文字コード
          <select value={encoding} onChange={(e) => setEncoding(e.target.value as Encoding)}>
            <option value="auto">自動で判定</option>
            <option value="shift_jis">Shift_JIS(多くの銀行)</option>
            <option value="utf-8">UTF-8</option>
          </select>
        </label>
        <label>
          読み飛ばす先頭の行数
          <input
            type="number"
            min={0}
            max={20}
            value={form.skip_rows}
            onChange={(e) => setForm((f) => ({ ...f, skip_rows: Math.max(0, Math.min(20, Number(e.target.value) || 0)) }))}
          />
        </label>
      </div>

      {!file ? (
        <label>
          列を確かめるため、この銀行のCSVファイルを選んでください
          <input
            type="file"
            accept=".csv,.txt,text/csv"
            onChange={async (e) => {
              const picked = e.target.files?.[0];
              if (!picked) return;
              try {
                const f = await readFile(picked);
                setFile(f);
                onFile(f);
              } catch (err) {
                setError((err as Error).message);
              }
            }}
          />
        </label>
      ) : (
        preview && (
          <>
            <div className="field-row roles">
              {ROLE_LABELS.map((r) => (
                <label key={r.key}>
                  {r.label}
                  <select
                    value={form[r.key] ?? -1}
                    aria-invalid={r.required && (form[r.key] ?? -1) < 0}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setForm((f) => ({ ...f, [r.key]: v < 0 ? (r.required ? -1 : null) : v }));
                    }}
                  >
                    <option value={-1}>{r.required ? "選んでください" : "使わない"}</option>
                    {Array.from({ length: columnCount }, (_, i) => (
                      <option key={i} value={i}>
                        {i + 1}列目{sampleOf(i) ? `: ${sampleOf(i).slice(0, 16)}` : ""}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            <p className="hint">
              {preview.encoding === "shift_jis" ? "Shift_JIS" : "UTF-8"}で読みました(全{preview.total_rows}行)。灰色の行は読み飛ばします。
            </p>
            <div className="table-wrap preview-wrap">
              <table className="csv-preview">
                <thead>
                  <tr>
                    {Array.from({ length: columnCount }, (_, i) => (
                      <th key={i} scope="col" className={roleOf(i) ? "is-role" : undefined}>
                        {i + 1}列目
                        {roleOf(i) && <span className="role-tag">{roleOf(i)}</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((row, r) => (
                    <tr key={r} className={r < form.skip_rows ? "is-skipped" : undefined}>
                      {Array.from({ length: columnCount }, (_, i) => (
                        <td key={i} className={roleOf(i) ? "is-role" : undefined}>
                          {row[i] ?? ""}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )
      )}

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="actions inline">
        <button type="button" onClick={save} disabled={busy}>
          {busy ? "保存しています…" : "この形式で保存"}
        </button>
        <button type="button" className="secondary" onClick={onCancel} disabled={busy}>
          やめる
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// 未消込の明細
// ---------------------------------------------------------------------

const BASIS_LABEL: Record<MatchCandidate["basis"], string> = {
  name_amount: "名義・金額が一致",
  amount: "金額が一致",
  name: "名義が一致",
};

const IGNORE_REASONS = ["預金利息", "返金・返戻", "売上以外の入金(借入・出資など)", "現金の預け入れ", "その他"];

type Choice =
  | { kind: "candidate"; index: number }
  | { kind: "manual"; customerId: string; invoiceIds: string[]; useFee: boolean };

function sumBalances(invoices: { balance: string }[]): number {
  return invoices.reduce((t, i) => t + Number(i.balance), 0);
}

function StatementCard({
  tenantId,
  statement: s,
  feeLimit,
  customers,
  onDone,
}: {
  tenantId: string;
  statement: BankStatement;
  feeLimit: number;
  customers: Customer[] | null;
  onDone: (text: string) => void;
}) {
  const [choice, setChoice] = useState<Choice | null>(s.candidates.length ? { kind: "candidate", index: 0 } : null);
  const [expanded, setExpanded] = useState(s.candidates.length === 0);
  const [manualInvoices, setManualInvoices] = useState<OpenInvoice[] | null>(null);
  const [remember, setRemember] = useState(true);
  const [ignoring, setIgnoring] = useState(false);
  const [reason, setReason] = useState(IGNORE_REASONS[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const amount = Number(s.amount);
  const manual = choice?.kind === "manual" ? choice : null;

  useEffect(() => {
    if (!manual?.customerId) {
      setManualInvoices(null);
      return;
    }
    let alive = true;
    api
      .openInvoices(tenantId, manual.customerId)
      .then((r) => alive && setManualInvoices(r.invoices))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [tenantId, manual?.customerId]);

  // 選んでいる内容で確定したらどうなるか
  const plan = useMemo(() => {
    if (!choice) return null;
    if (choice.kind === "candidate") {
      const c = s.candidates[choice.index];
      const total = sumBalances(c.invoices);
      return { customerId: c.customer_id, customerName: c.customer_name, invoiceIds: c.invoices.map((i) => i.invoice_id), total, fee: Number(c.fee_amount), nameKnown: c.basis !== "amount" };
    }
    const picked = (manualInvoices ?? []).filter((i) => choice.invoiceIds.includes(i.invoice_id));
    const total = sumBalances(picked);
    const diff = total - amount;
    const feeOk = diff > 0 && diff <= feeLimit;
    const customer = customers?.find((c) => c.id === choice.customerId);
    return {
      customerId: choice.customerId,
      customerName: customer?.name ?? "",
      invoiceIds: picked.map((i) => i.invoice_id),
      total,
      fee: feeOk && choice.useFee ? diff : 0,
      nameKnown: false,
    };
  }, [choice, s.candidates, manualInvoices, customers, amount, feeLimit]);

  const diff = plan ? plan.total - amount : 0;
  const problem = !plan || plan.invoiceIds.length === 0 ? "消し込む請求書を選んでください" : diff < 0 ? `入金額が選んだ請求書の残額の合計(${yen(plan.total)})を超えています` : "";

  async function confirm() {
    if (!plan || problem) return;
    setBusy(true);
    setError("");
    try {
      await api.matchStatement(tenantId, s.id, {
        customer_id: plan.customerId,
        invoice_ids: plan.invoiceIds,
        fee_amount: String(plan.fee),
        remember_payer: !plan.nameKnown && remember,
      });
      onDone(`${s.payer_name} ${yen(s.amount)}を${plan.customerName}の請求書に消し込みました`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function ignore() {
    setBusy(true);
    try {
      await api.ignoreStatement(tenantId, s.id, reason);
      onDone(`${s.payer_name} ${yen(s.amount)}を対象外にしました`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <article className="statement">
      <header className="statement-head">
        <div>
          <p className="statement-date">{jdate(s.txn_date)}</p>
          <p className="statement-payer">{s.payer_name}</p>
          {s.memo && <p className="statement-memo">{s.memo}</p>}
        </div>
        <p className="statement-amount">{yen(s.amount)}</p>
      </header>

      {s.candidates.length > 0 && (
        <div className="candidates" role="radiogroup" aria-label="消込の候補">
          {s.candidates.map((c, index) => {
            const selected = choice?.kind === "candidate" && choice.index === index;
            if (!expanded && index > 0) return null;
            return (
              <label key={index} className={selected ? "candidate is-selected" : "candidate"}>
                <input type="radio" name={`cand-${s.id}`} checked={selected} onChange={() => setChoice({ kind: "candidate", index })} />
                <span className="candidate-body">
                  <span className="candidate-title">
                    {c.customer_name}
                    <span className={`badge inline basis-${c.basis}`}>{BASIS_LABEL[c.basis]}</span>
                  </span>
                  <span className="candidate-invoices">
                    {c.invoices.map((i) => (
                      <span key={i.invoice_id}>
                        {i.invoice_number}
                        {i.version > 1 && `(第${i.version}版)`} 残額 {yen(i.balance)}
                      </span>
                    ))}
                  </span>
                  <span className="candidate-result">{resultText(c.match, Number(c.fee_amount), sumBalances(c.invoices) - amount)}</span>
                </span>
              </label>
            );
          })}
        </div>
      )}
      {s.candidates.length === 0 && <p className="hint">候補が見つかりません。取引先と請求書を選んでください。</p>}

      {expanded && (
        <div className={manual ? "manual is-selected" : "manual"}>
          <label className="candidate">
            <input
              type="radio"
              name={`cand-${s.id}`}
              checked={!!manual}
              onChange={() => setChoice({ kind: "manual", customerId: "", invoiceIds: [], useFee: true })}
            />
            <span className="candidate-title">取引先と請求書を選ぶ</span>
          </label>
          {manual && (
            <div className="manual-body">
              <CustomerSelect
                customers={customers}
                value={manual.customerId}
                invalid={false}
                includeInactive
                onChange={(id) => setChoice({ ...manual, customerId: id, invoiceIds: [] })}
              />
              {manualInvoices &&
                (manualInvoices.length === 0 ? (
                  <p className="hint">この取引先に未入金の請求書はありません。</p>
                ) : (
                  <ul className="pick-list">
                    {manualInvoices.map((i) => (
                      <li key={i.invoice_id}>
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={manual.invoiceIds.includes(i.invoice_id)}
                            onChange={(e) =>
                              setChoice({
                                ...manual,
                                invoiceIds: e.target.checked
                                  ? [...manual.invoiceIds, i.invoice_id]
                                  : manual.invoiceIds.filter((x) => x !== i.invoice_id),
                              })
                            }
                          />
                          {i.invoice_number}
                          {i.version > 1 && `(第${i.version}版)`}
                          <span className="pick-meta">
                            {jdate(i.issue_date)} ・ 残額 {yen(i.balance)}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                ))}
              {plan && plan.invoiceIds.length > 0 && diff > 0 && diff <= feeLimit && (
                <label className="check">
                  <input type="checkbox" checked={manual.useFee} onChange={(e) => setChoice({ ...manual, useFee: e.target.checked })} />
                  差額 {yen(String(diff))}を振込手数料として消し込む
                </label>
              )}
              {plan && plan.invoiceIds.length > 0 && !problem && (
                <p className="candidate-result">{resultText(diff === 0 ? "exact" : plan.fee ? "fee" : "partial", plan.fee, diff)}</p>
              )}
            </div>
          )}
        </div>
      )}

      {plan && !plan.nameKnown && plan.customerId && (
        <label className="check remember">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          「{s.payer_name}」を{plan.customerName || "この取引先"}の振込名義として登録する(次から名義で候補に出ます)
        </label>
      )}

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      <div className="statement-actions">
        <button type="button" onClick={confirm} disabled={busy || !!problem} title={problem || undefined}>
          確定
        </button>
        {!expanded && (
          <button type="button" className="secondary" onClick={() => setExpanded(true)} disabled={busy}>
            {s.candidates.length > 1 ? "ほかの候補・請求書を選ぶ" : "別の請求書を選ぶ"}
          </button>
        )}
        {ignoring ? (
          <span className="ignore-form">
            <select value={reason} onChange={(e) => setReason(e.target.value)} aria-label="対象外にする理由">
              {IGNORE_REASONS.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
            <button type="button" className="secondary" onClick={ignore} disabled={busy}>
              対象外にする
            </button>
            <button type="button" className="link-button" onClick={() => setIgnoring(false)}>
              やめる
            </button>
          </span>
        ) : (
          <button type="button" className="link-button" onClick={() => setIgnoring(true)} disabled={busy}>
            請求書と関係のない入金
          </button>
        )}
        {problem && plan && <span className="hint">{problem}</span>}
      </div>
    </article>
  );
}

function resultText(match: MatchCandidate["match"], fee: number, diff: number): string {
  if (match === "exact") return "ちょうど消し込めます";
  if (match === "fee") return `振込手数料 ${yen(String(fee))}を差し引いて、入金済みにします`;
  return `一部入金になります(残り ${yen(String(diff))})`;
}

// ---------------------------------------------------------------------
// 消込済・対象外
// ---------------------------------------------------------------------

function ProcessedTable({
  tenantId,
  statements,
  kind,
  onDone,
  onError,
}: {
  tenantId: string;
  statements: BankStatement[];
  kind: "matched" | "ignored";
  onDone: (text: string) => void;
  onError: (text: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function undo(s: BankStatement) {
    const text =
      kind === "matched"
        ? `${s.payer_name} ${yen(s.amount)}の消込を取り消しますか?\n登録した入金も取り消され、請求書は未入金に戻ります。`
        : `${s.payer_name} ${yen(s.amount)}を未消込に戻しますか?`;
    if (!window.confirm(text)) return;
    setBusy(true);
    try {
      if (kind === "matched") await api.unmatchStatement(tenantId, s.id);
      else await api.unignoreStatement(tenantId, s.id);
      onDone(kind === "matched" ? "消込を取り消しました" : "未消込に戻しました");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">取引日</th>
            <th scope="col">振込名義</th>
            <th scope="col" className="num">
              入金額
            </th>
            {kind === "matched" ? (
              <>
                <th scope="col">取引先</th>
                <th scope="col">請求書</th>
                <th scope="col" className="num">
                  振込手数料
                </th>
              </>
            ) : (
              <th scope="col">理由</th>
            )}
            <th scope="col">
              <span className="visually-hidden">操作</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {statements.map((s) => (
            <tr key={s.id}>
              <td>{jdate(s.txn_date)}</td>
              <td>{s.payer_name}</td>
              <td className="num">{yen(s.amount)}</td>
              {kind === "matched" ? (
                <>
                  <td>{s.receipt?.customer_name}</td>
                  <td>{s.receipt?.invoice_numbers.join("、")}</td>
                  <td className="num">{s.receipt && Number(s.receipt.fee_amount) > 0 ? yen(s.receipt.fee_amount) : "―"}</td>
                </>
              ) : (
                <td>{s.ignore_reason ?? "―"}</td>
              )}
              <td>
                <button type="button" className="link-button" onClick={() => undo(s)} disabled={busy}>
                  {kind === "matched" ? "確定を取り消す" : "未消込に戻す"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
