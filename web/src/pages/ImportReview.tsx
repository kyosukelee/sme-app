import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Customer, type DraftLine, type ImportCheck, type ImportDetail, type ImportDraft } from "../api";
import LineTable from "../components/LineTable";
import TotalsList from "../components/TotalsList";
import { useCompany } from "../company";
import { jdatetime, yen } from "../format";
import { blankRow, isBlank, normalizeNumber, type Row } from "../lines";
import { navigate } from "../router";
import { useLiveCalc } from "../useLiveCalc";
import { CustomerSelect } from "./QuoteEditor";
import { ImportStatusBadge } from "./ImportList";

/** 注文書の取り込み1件。読み取り中は待ち、要確認なら元のPDFと見比べながら直して確定する */
export default function ImportReview({ tenantId, id }: { tenantId: string; id: string }) {
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let timer: number | undefined;
    let alive = true;
    api
      .importDetail(tenantId, id)
      .then((d) => {
        if (!alive) return;
        setDetail(d);
        if (d.status === "queued" || d.status === "processing") timer = window.setTimeout(() => setReload((n) => n + 1), 2000);
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [tenantId, id, reload]);

  const head = (
    <a className="back" href="#/imports">
      注文書の取り込み
    </a>
  );
  if (error) {
    return (
      <>
        {head}
        <p className="alert" role="alert">
          {error}
        </p>
      </>
    );
  }
  if (!detail) return <p className="placeholder">読み込み中…</p>;

  return (
    <>
      <div className="page-head">
        {head}
        <h1 className="file-title">{detail.file_name}</h1>
        <ImportStatusBadge status={detail.status} />
        {detail.order_id && (
          <a className="source-link" href={`#/orders/${detail.order_id}`}>
            受注 {detail.order_number}
          </a>
        )}
      </div>

      <div className="import-review">
        <PdfPane tenantId={tenantId} id={id} />
        <div className="stack">
          {detail.status === "queued" || detail.status === "processing" ? (
            <section className="panel">
              <p>AIが読み取っています。このままお待ちください(通常は数秒〜1分ほどです)。</p>
            </section>
          ) : detail.status === "failed" ? (
            <FailedPanel tenantId={tenantId} detail={detail} onChange={setDetail} onRetry={() => setReload((n) => n + 1)} />
          ) : detail.status === "review" && detail.draft ? (
            <ReviewForm key={detail.id} tenantId={tenantId} initial={detail} />
          ) : (
            <ClosedPanel detail={detail} />
          )}
          <ReadingInfo detail={detail} />
        </div>
      </div>
    </>
  );
}

/** 元のPDF(画面の中に表示する) */
function PdfPane({ tenantId, id }: { tenantId: string; id: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let created: string | null = null;
    api
      .importPdfUrl(tenantId, id)
      .then((u) => {
        created = u;
        setUrl(u);
      })
      .catch((e: Error) => setError(e.message));
    return () => {
      if (created) URL.revokeObjectURL(created);
    };
  }, [tenantId, id]);
  return (
    <section className="panel pdf-pane" aria-label="取り込んだ注文書">
      {error ? <p className="alert">{error}</p> : url ? <iframe title="取り込んだ注文書" src={url} /> : <p className="hint">PDFを読み込んでいます…</p>}
    </section>
  );
}

function FailedPanel({
  tenantId,
  detail,
  onChange,
  onRetry,
}: {
  tenantId: string;
  detail: ImportDetail;
  onChange: (d: ImportDetail) => void;
  onRetry: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const notOrder = detail.error_message?.includes("と判定されました") ?? false;

  async function run(action: () => Promise<ImportDetail>) {
    setBusy(true);
    setMessage("");
    try {
      onChange(await action());
      onRetry();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>読み取れませんでした</h2>
      <p className="alert">{detail.error_message}</p>
      <div className="actions inline">
        {notOrder ? (
          <button type="button" onClick={() => run(() => api.retryImport(tenantId, detail.id, true))} disabled={busy}>
            注文書として読み直す
          </button>
        ) : (
          <button type="button" onClick={() => run(() => api.retryImport(tenantId, detail.id))} disabled={busy}>
            やり直す
          </button>
        )}
        <DiscardButton tenantId={tenantId} id={detail.id} disabled={busy} onDone={onChange} />
      </div>
      {message && <p className="alert">{message}</p>}
    </section>
  );
}

function ClosedPanel({ detail }: { detail: ImportDetail }) {
  if (detail.status === "confirmed") {
    return (
      <section className="panel">
        <p className="notice">
          {detail.confirmed_at && `${jdatetime(detail.confirmed_at)} に`}確定し、受注{" "}
          {detail.order_id ? <a href={`#/orders/${detail.order_id}`}>{detail.order_number}</a> : "(削除済み)"} を登録しました。
        </p>
      </section>
    );
  }
  return (
    <section className="panel">
      <p className="notice-warn">この取り込みは破棄しました。{detail.discard_reason && `理由: ${detail.discard_reason}`}</p>
      <p className="hint">取り込んだPDFと読み取りの記録は残しています。同じPDFは、もう一度取り込めます。</p>
    </section>
  );
}

/** 破棄(理由は任意)。確認はブラウザのダイアログを使わず、画面の中に出す */
function DiscardButton({ tenantId, id, disabled, onDone }: { tenantId: string; id: string; disabled: boolean; onDone: (d: ImportDetail) => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function discard() {
    setBusy(true);
    setError("");
    try {
      onDone(await api.discardImport(tenantId, id, reason.trim() || null));
      setOpen(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="danger-text" onClick={() => setOpen(true)} disabled={disabled}>
        破棄する
      </button>
    );
  }
  return (
    <div className="notice-warn confirm-box">
      <label>
        破棄する理由(任意)
        <input value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="例: 請求書だった、二重に届いた" />
      </label>
      <p>取り込んだPDFと読み取りの記録は残ります。</p>
      <div className="actions inline">
        <button type="button" onClick={discard} disabled={busy}>
          破棄する
        </button>
        <button type="button" className="secondary" onClick={() => setOpen(false)} disabled={busy}>
          やめる
        </button>
      </div>
      {error && <p className="alert">{error}</p>}
    </div>
  );
}

/** AI の読み取りの記録(どのAIで、どれだけかかったか) */
function ReadingInfo({ detail }: { detail: ImportDetail }) {
  const e = detail.extracted;
  if (!detail.provider && !e) return null;
  return (
    <section className="panel reading-info">
      <h2>AIの読み取り</h2>
      <dl>
        {e && (
          <>
            <div>
              <dt>注文主</dt>
              <dd>
                {e.issuer_name ?? "―"}
                {e.issuer_address && <small>{e.issuer_address}</small>}
              </dd>
            </div>
            <div>
              <dt>宛先</dt>
              <dd>{e.recipient_name ?? "―"}</dd>
            </div>
            <div>
              <dt>注文書の合計</dt>
              <dd>
                {yen(normalizeNumber(e.total ?? ""))}
                <small>
                  小計 {yen(normalizeNumber(e.subtotal ?? ""))} / 消費税 {yen(normalizeNumber(e.tax_total ?? ""))}
                </small>
              </dd>
            </div>
          </>
        )}
        <div>
          <dt>AI</dt>
          <dd>
            {detail.model ?? "―"}
            <small>
              {detail.duration_ms !== null && `${(detail.duration_ms / 1000).toFixed(1)}秒`}
              {detail.input_tokens !== null && ` ・ 入力 ${detail.input_tokens.toLocaleString()} / 出力 ${(detail.output_tokens ?? 0).toLocaleString()} トークン`}
              {detail.prompt_version && ` ・ 指示文 ${detail.prompt_version}`}
            </small>
          </dd>
        </div>
      </dl>
    </section>
  );
}

// ---------------------------------------------------------------------
// 確認と修正
// ---------------------------------------------------------------------
type Extra = Pick<DraftLine, "item_id" | "item_code" | "doc_amount" | "doc_tax_rate" | "page">;

function toRows(lines: DraftLine[]): { rows: Row[]; extras: Map<number, Extra> } {
  const extras = new Map<number, Extra>();
  const rows = (lines.length ? lines : [{ description: "", quantity: "1", unit: "", unit_price: "", tax_category: "standard", item_id: null } as DraftLine]).map(
    (l) => {
      const row: Row = { ...blankRow(), description: l.description, quantity: l.quantity, unit: l.unit, unit_price: l.unit_price, tax_category: l.tax_category };
      extras.set(row.key, { item_id: l.item_id, item_code: l.item_code, doc_amount: l.doc_amount, doc_tax_rate: l.doc_tax_rate, page: l.page });
      return row;
    },
  );
  return { rows, extras };
}

function ReviewForm({ tenantId, initial }: { tenantId: string; initial: ImportDetail }) {
  const { taxExempt, items } = useCompany();
  const [detail, setDetail] = useState(initial);
  const draft0 = initial.draft!;
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [customerId, setCustomerId] = useState(draft0.customer_id ?? "");
  const [orderRef, setOrderRef] = useState(draft0.customer_order_ref);
  const [orderDate, setOrderDate] = useState(draft0.order_date);
  const [dueDate, setDueDate] = useState(draft0.delivery_due_date);
  const [title, setTitle] = useState(draft0.title);
  const [remarks, setRemarks] = useState(draft0.remarks);
  const initialRows = useMemo(() => toRows(draft0.lines), [draft0.lines]);
  const [rows, setRows] = useState<Row[]>(initialRows.rows);
  const extras = useRef(initialRows.extras);
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "confirm">("");
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  const { calc, amountByKey } = useLiveCalc(tenantId, rows, orderDate || null, null, (text) => setMessage({ kind: "error", text }));

  useEffect(() => {
    api.customers(tenantId).then(setCustomers).catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  function change(patch: () => void) {
    patch();
    setDirty(true);
    setMessage(null);
  }

  function collect(): ImportDraft {
    return {
      customer_id: customerId || null,
      customer_order_ref: orderRef.trim(),
      order_date: orderDate,
      delivery_due_date: dueDate,
      title: title.trim(),
      remarks: remarks.trim(),
      lines: rows
        .filter((r) => !isBlank(r))
        .map((r) => {
          const extra = extras.current.get(r.key) ?? { item_id: null };
          const item = items.find((i) => i.name === r.description.trim());
          return {
            description: r.description.trim(),
            quantity: normalizeNumber(r.quantity),
            unit: r.unit.trim(),
            unit_price: normalizeNumber(r.unit_price),
            tax_category: r.tax_category,
            ...extra,
            item_id: item?.id ?? extra.item_id ?? null,
          };
        }),
    };
  }

  /** 保存して検証し直す。保存後の取り込みを返す */
  async function save(): Promise<ImportDetail> {
    const saved = await api.saveImportDraft(tenantId, detail.id, collect());
    setDetail(saved);
    setDirty(false);
    return saved;
  }

  async function handleSave() {
    setBusy("save");
    try {
      const saved = await save();
      const errors = saved.checks.filter((c) => c.level === "error").length;
      setMessage({ kind: "done", text: errors ? `保存しました。直す項目が${errors}件あります。` : "保存し、検証し直しました。" });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function handleConfirm() {
    setBusy("confirm");
    setMessage(null);
    try {
      const saved = dirty ? await save() : detail;
      const errors = saved.checks.filter((c) => c.level === "error");
      const pending = saved.checks.filter((c) => c.level === "warning" && !acknowledged.has(c.id));
      if (errors.length || pending.length) {
        setMessage({
          kind: "error",
          text: errors.length
            ? errors.every((c) => c.id === "customer_missing")
              ? "取引先を選んでから確定してください。"
              : "「要修正」の項目を直してから確定してください。"
            : "「要確認」の項目をすべて確かめて、チェックを入れてから確定してください。",
        });
        return;
      }
      const done = await api.confirmImport(tenantId, saved.id, saved.draft!, [...acknowledged]);
      navigate(`/orders/${done.order_id}`);
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  const checks = detail.checks;
  const errors = checks.filter((c) => c.level === "error");
  const warnings = checks.filter((c) => c.level === "warning");
  const invalid = (field: string) => !dirty && errors.some((c) => c.field === field);
  const result = calc?.result;
  const docTotal = detail.extracted?.total ? normalizeNumber(detail.extracted.total) : null;

  return (
    <>
      <ChecksPanel
        errors={errors}
        warnings={warnings}
        acknowledged={acknowledged}
        stale={dirty}
        onToggle={(id) =>
          setAcknowledged((set) => {
            const next = new Set(set);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          })
        }
      />

      <section className="panel">
        <h2>取引先</h2>
        {detail.customer_candidates.length > 0 ? (
          <div className="candidates" role="radiogroup" aria-label="取引先の候補">
            {detail.customer_candidates.map((c) => (
              <label key={c.customer_id} className={customerId === c.customer_id ? "candidate is-selected" : "candidate"}>
                <input type="radio" name="customer-candidate" checked={customerId === c.customer_id} onChange={() => change(() => setCustomerId(c.customer_id))} />
                <span className="candidate-body">
                  <span className="candidate-title">{c.name}</span>
                  <span className="candidate-invoices">{c.reasons.join("・")}</span>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="hint">注文主「{detail.extracted?.issuer_name ?? "(読み取れず)"}」に近い取引先が見つかりません。下から選ぶか、先に取引先を追加してください。</p>
        )}
        <div className="field-grid single">
          <CustomerSelect customers={customers} value={customerId} invalid={false} onChange={(v) => change(() => setCustomerId(v))} />
        </div>
        <p className="hint">確定すると、注文書上の名前「{detail.extracted?.issuer_name ?? "―"}」をこの取引先に覚え、次からは最初から選びます。</p>
      </section>

      <section className="panel">
        <h2>注文の内容</h2>
        <div className="field-grid">
          <label>
            お客様の注文番号
            <input value={orderRef} maxLength={50} aria-invalid={invalid("customer_order_ref")} onChange={(e) => change(() => setOrderRef(e.target.value))} />
          </label>
          <label>
            件名
            <input value={title} onChange={(e) => change(() => setTitle(e.target.value))} />
          </label>
          <label>
            受注日(注文日)
            <input type="date" value={orderDate} onChange={(e) => change(() => setOrderDate(e.target.value))} />
          </label>
          <label>
            納期
            <input type="date" value={dueDate} onChange={(e) => change(() => setDueDate(e.target.value))} />
          </label>
        </div>
        <label>
          備考(注文請書に印字されます)
          <textarea rows={2} value={remarks} onChange={(e) => change(() => setRemarks(e.target.value))} />
        </label>
      </section>

      <section className="panel lines-panel">
        <h2>明細</h2>
        <LineTable rows={rows} onChange={(u) => change(() => setRows(u))} amountByKey={amountByKey} showErrors={!dirty && errors.some((c) => c.id.startsWith("line_invalid"))} showTax={!taxExempt} />
        <p className="hint">単価は税抜きです。品名に商品・サービスの名前を選ぶと、単位・単価・税率も入ります。</p>
      </section>

      <section className="panel totals">
        <h2>合計(自社の計算)</h2>
        <TotalsList
          summaries={result?.tax_summaries ?? []}
          subtotal={result?.subtotal_amount ?? 0}
          tax={result?.tax_amount ?? 0}
          total={result?.total_amount ?? 0}
          exempt={taxExempt}
        />
        {docTotal && (
          <p className={result && Number(result.total_amount) !== Number(docTotal) ? "notice-warn" : "hint"}>
            注文書の合計: {yen(docTotal)}
            {result && Number(result.total_amount) !== Number(docTotal) && `(差額 ${yen(Number(result.total_amount) - Number(docTotal))})`}
          </p>
        )}
        <div className="actions">
          <button type="button" onClick={handleConfirm} disabled={busy !== ""}>
            {busy === "confirm" ? "確定しています…" : "確定して受注を登録"}
          </button>
          <button type="button" className="secondary" onClick={handleSave} disabled={busy !== "" || !dirty}>
            {busy === "save" ? "保存しています…" : "保存して検証し直す"}
          </button>
          <DiscardButton tenantId={tenantId} id={detail.id} disabled={busy !== ""} onDone={() => navigate("/imports")} />
        </div>
        {dirty && <p className="hint">未保存の変更があります。確定するときに保存し、検証し直します。</p>}
        {message && (
          <p className={message.kind === "done" ? "notice" : "alert"} role="status">
            {message.text}
          </p>
        )}
      </section>
    </>
  );
}

function ChecksPanel({
  errors,
  warnings,
  acknowledged,
  stale,
  onToggle,
}: {
  errors: ImportCheck[];
  warnings: ImportCheck[];
  acknowledged: Set<string>;
  stale: boolean;
  onToggle: (id: string) => void;
}) {
  if (errors.length === 0 && warnings.length === 0) {
    return (
      <section className="panel">
        <p className="notice">確認が必要な項目はありません。内容を見て確定してください。</p>
      </section>
    );
  }
  return (
    <section className="panel checks-panel">
      <h2>確認すること</h2>
      {stale && <p className="hint">変更後の検証は、保存したときに行います。</p>}
      {errors.length > 0 && (
        <ul className="check-list">
          {errors.map((c) => (
            // 取引先の選択は、AI の読み取りの誤りではなく、毎回人が行う手順なので落ち着いた色にする
            <li key={c.id} className={c.id === "customer_missing" ? "is-todo" : "is-error"}>
              {c.id === "customer_missing" ? "取引先を選んでください(候補から選ぶか、一覧から選びます)。" : `要修正: ${c.message}`}
            </li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul className="check-list">
          {warnings.map((c) => (
            <li key={c.id} className="is-warning">
              <label>
                <input type="checkbox" checked={acknowledged.has(c.id)} onChange={() => onToggle(c.id)} />
                <span>要確認: {c.message}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && <p className="hint">「要確認」の項目は、内容を確かめてチェックを入れると、そのまま確定できます。</p>}
    </section>
  );
}
