import { useCallback, useEffect, useState } from "react";
import {
  api,
  type AccountKey,
  type JournalEntry,
  type JournalExport,
  type JournalPreview,
  type JournalSettings,
  type Software,
  type TaxKey,
} from "../api";
import { jdate, yen } from "../format";

type Message = { kind: "done" | "error"; text: string } | null;

const SOFTWARE: { value: Software; label: string }[] = [
  { value: "kanjo_bugyo", label: "勘定奉行" },
  { value: "yayoi", label: "弥生会計" },
  { value: "freee", label: "freee会計" },
  { value: "mf", label: "マネーフォワード クラウド会計" },
];

const ACCOUNTS: { key: AccountKey; label: string; hint: string }[] = [
  { key: "receivable", label: "売掛金", hint: "請求書を発行したとき" },
  { key: "sales", label: "売上", hint: "請求書の税率ごと" },
  { key: "bank", label: "入金口座", hint: "補助科目に銀行名を入れられます" },
  { key: "fee", label: "振込手数料", hint: "取引先が差し引いた手数料" },
  { key: "withholding", label: "源泉徴収税額", hint: "法人は仮払税金、個人事業主は事業主貸が一般的" },
];

const TAXES: { key: TaxKey; label: string }[] = [
  { key: "sales_standard", label: "課税売上 10%" },
  { key: "sales_reduced", label: "課税売上 軽減8%" },
  { key: "sales_exempt", label: "非課税売上" },
  { key: "sales_non_taxable", label: "不課税の売上" },
  { key: "sales_export", label: "輸出売上(免税)" },
  { key: "out_of_scope", label: "対象外(売掛金・預金など)" },
  { key: "purchase_standard", label: "課税仕入 10%(振込手数料)" },
];

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function monthRange(offset: number): [string, string] {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth() + offset, 1);
  const last = new Date(now.getFullYear(), now.getMonth() + offset + 1, 0);
  return [iso(first), iso(last)];
}

/** 会計ソフト向けの仕訳CSV。発行した請求書(売上)と入金を、会計ソフトの取り込み形式で出力する */
export default function Journal({ tenantId }: { tenantId: string }) {
  const [[from, to], setRange] = useState<[string, string]>(() => monthRange(-1));
  const [preview, setPreview] = useState<JournalPreview | null>(null);
  const [exports, setExports] = useState<JournalExport[]>([]);
  const [settings, setSettings] = useState<JournalSettings | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  const load = useCallback(async () => {
    const [p, e] = await Promise.all([api.journalPending(tenantId, from, to), api.journalExports(tenantId)]);
    setPreview(p);
    setExports(e);
  }, [tenantId, from, to]);

  useEffect(() => {
    if (!from || !to || to < from) return;
    setPreview(null);
    load().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [load, from, to]);

  useEffect(() => {
    api.journalSettings(tenantId).then(setSettings).catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  async function runExport() {
    if (!preview) return;
    const text = `${preview.entries.length}件の仕訳を${preview.software_name}の形式で出力しますか?\n出力した仕訳は「出力済み」になり、次からは出力されません。`;
    if (!window.confirm(text)) return;
    setBusy(true);
    setMessage(null);
    try {
      const created = await api.createJournalExport(tenantId, from, to);
      const replaced = await api.downloadJournal(tenantId, created.id);
      setMessage({
        kind: "done",
        text:
          `${created.entry_count}件の仕訳を出力しました。会計ソフトで取り込んでください。` +
          (replaced ? `(Shift_JIS で表せない文字 ${replaced}文字を「?」に置き換えています)` : ""),
      });
      await load();
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function redownload(x: JournalExport) {
    try {
      await api.downloadJournal(tenantId, x.id);
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    }
  }

  async function cancel(x: JournalExport) {
    if (!window.confirm("この出力を取り消しますか?\n会計ソフトにまだ取り込んでいない場合だけ取り消してください。取り消した仕訳は、また出力の対象に戻ります。"))
      return;
    try {
      await api.deleteJournalExport(tenantId, x.id);
      setMessage({ kind: "done", text: "出力を取り消しました" });
      await load();
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    }
  }

  const reversals = preview?.entries.filter((e) => e.kind === "reversal").length ?? 0;

  return (
    <>
      <div className="page-head">
        <h1>仕訳の出力</h1>
        {settings && <p className="count">出力先: {SOFTWARE.find((s) => s.value === settings.software)?.label}</p>}
      </div>
      <p className="lead">
        発行した請求書(売上)と入金を仕訳にして、会計ソフトの取り込み形式の CSV で出力します。一度出力した仕訳は二重に出しません。出力したあとに訂正・取消したものは、次の出力で取消の仕訳(逆仕訳)を出します。
      </p>

      {message && (
        <p className={message.kind === "done" ? "notice" : "alert"} role="status">
          {message.text}
        </p>
      )}

      <section className="panel journal-panel">
        <div className="journal-period">
          <label>
            期間(仕訳の日付)
            <span className="period-inputs">
              <input type="date" value={from} onChange={(e) => setRange([e.target.value, to])} aria-label="期間の始まり" />
              〜
              <input type="date" value={to} onChange={(e) => setRange([from, e.target.value])} aria-label="期間の終わり" />
            </span>
          </label>
          <div className="actions inline">
            <button type="button" className="secondary" onClick={() => setRange(monthRange(-1))}>
              先月
            </button>
            <button type="button" className="secondary" onClick={() => setRange(monthRange(0))}>
              今月
            </button>
          </div>
        </div>

        {to < from ? (
          <p className="alert">期間の終わりは、始まりより後の日付にしてください。</p>
        ) : preview === null ? (
          <p className="placeholder">読み込み中…</p>
        ) : (
          <>
            {preview.problem && (
              <p className="alert" role="alert">
                {preview.problem}
              </p>
            )}
            {preview.before_count > 0 && (
              <p className="hint warn">
                この期間より前の日付で、まだ出力していない仕訳が{preview.before_count}件あります。期間の始まりを前にすると含められます。
              </p>
            )}
            {reversals > 0 && <p className="hint">出力したあとに訂正・取消したものの取消の仕訳が{reversals}件あります。</p>}
            {preview.entries.length === 0 ? (
              <p className="placeholder">この期間に、まだ出力していない仕訳はありません。</p>
            ) : (
              <EntryTable entries={preview.entries} />
            )}
            {preview.entries.length > 0 && (
              <div className="actions inline">
                <button type="button" onClick={runExport} disabled={busy || !!preview.problem}>
                  {busy ? "出力しています…" : `${preview.software_name}の形式で出力(${preview.entries.length}件)`}
                </button>
              </div>
            )}
          </>
        )}
      </section>

      {exports.length > 0 && (
        <section className="panel">
          <h2>出力の履歴</h2>
          <ul className="history">
            {exports.map((x) => (
              <li key={x.id}>
                <span>
                  {jdate(x.date_from)}〜{jdate(x.date_to)} <strong>{x.entry_count}件</strong>
                  <small>
                    {x.software_name} ・ {jdate(x.created_at.slice(0, 10))}に出力
                  </small>
                </span>
                <span className="history-actions">
                  <button type="button" className="link-button" onClick={() => redownload(x)}>
                    もう一度ダウンロード
                  </button>
                  {x.is_latest && (
                    <button type="button" className="link-button" onClick={() => cancel(x)}>
                      取り消す
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel">
        <button type="button" className="disclosure" aria-expanded={showSettings} onClick={() => setShowSettings((v) => !v)}>
          仕訳の設定(会計ソフト・勘定科目・税区分)
        </button>
        {showSettings && settings && (
          <SettingsForm
            tenantId={tenantId}
            settings={settings}
            onSaved={(s) => {
              setSettings(s);
              setMessage({ kind: "done", text: "仕訳の設定を保存しました" });
              load().catch(() => {});
            }}
          />
        )}
      </section>
    </>
  );
}

function SideCell({ s }: { s: JournalEntry["lines"][number]["debit"] }) {
  return (
    <>
      <td>
        {s.account}
        {s.sub && <small className="sub">{s.sub}</small>}
        <small className="tax">{s.tax}</small>
      </td>
      <td className="num">
        {yen(s.amount)}
        {Number(s.tax_amount) > 0 && <small className="tax">うち税 {yen(s.tax_amount)}</small>}
      </td>
    </>
  );
}

function EntryTable({ entries }: { entries: JournalEntry[] }) {
  return (
    <div className="table-wrap">
      <table className="journal-table">
        <thead>
          <tr>
            <th scope="col">日付</th>
            <th scope="col">摘要</th>
            <th scope="col">借方</th>
            <th scope="col" className="num">
              金額
            </th>
            <th scope="col">貸方</th>
            <th scope="col" className="num">
              金額
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) =>
            e.lines.map((l, j) => (
              <tr key={`${i}-${j}`} className={[j === 0 && "entry-first", e.kind === "reversal" && "is-reversal"].filter(Boolean).join(" ") || undefined}>
                {j === 0 && (
                  <>
                    <td rowSpan={e.lines.length}>{jdate(e.date)}</td>
                    <td rowSpan={e.lines.length}>{e.summary}</td>
                  </>
                )}
                <SideCell s={l.debit} />
                <SideCell s={l.credit} />
              </tr>
            )),
          )}
        </tbody>
      </table>
    </div>
  );
}

function SettingsForm({
  tenantId,
  settings,
  onSaved,
}: {
  tenantId: string;
  settings: JournalSettings;
  onSaved: (s: JournalSettings) => void;
}) {
  const [form, setForm] = useState(settings);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const disabled = !settings.can_edit;
  const bugyo = form.software === "kanjo_bugyo";
  const labels = form.tax_labels[form.software];

  const setAccount = (key: AccountKey, field: "name" | "code" | "sub", value: string) =>
    setForm((f) => ({ ...f, accounts: { ...f.accounts, [key]: { ...f.accounts[key], [field]: value } } }));
  const setLabel = (key: TaxKey, value: string) =>
    setForm((f) => ({ ...f, tax_labels: { ...f.tax_labels, [f.software]: { ...f.tax_labels[f.software], [key]: value } } }));

  async function save() {
    setBusy(true);
    setError("");
    try {
      const { software, sales_date_basis, customer_as_sub, accounts, tax_labels } = form;
      onSaved(await api.updateJournalSettings(tenantId, { software, sales_date_basis, customer_as_sub, accounts, tax_labels }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="journal-settings">
      {disabled && <p className="hint">仕訳の設定を変えられるのは管理者だけです。</p>}
      <div className="field-row">
        <label>
          会計ソフト
          <select value={form.software} disabled={disabled} onChange={(e) => setForm((f) => ({ ...f, software: e.target.value as Software }))}>
            {SOFTWARE.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          売上の計上日
          <select
            value={form.sales_date_basis}
            disabled={disabled}
            onChange={(e) => setForm((f) => ({ ...f, sales_date_basis: e.target.value as "transaction" | "issue" }))}
          >
            <option value="transaction">取引日(締め請求は締め日、それ以外は最後の納品日)</option>
            <option value="issue">請求日</option>
          </select>
        </label>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={form.customer_as_sub}
          disabled={disabled}
          onChange={(e) => setForm((f) => ({ ...f, customer_as_sub: e.target.checked }))}
        />
        売掛金の補助科目に取引先名を入れる
        <span className="hint">freee・マネーフォワードには、取引先の欄にも取引先名を入れます。</span>
      </label>

      <h3>勘定科目</h3>
      <div className="table-wrap flat">
        <table className="settings-table">
          <thead>
            <tr>
              <th scope="col">使い道</th>
              <th scope="col">勘定科目</th>
              <th scope="col">補助科目</th>
              {bugyo && <th scope="col">科目コード</th>}
            </tr>
          </thead>
          <tbody>
            {ACCOUNTS.map((a) => (
              <tr key={a.key}>
                <th scope="row">
                  {a.label}
                  <small>{a.hint}</small>
                </th>
                <td>
                  <input value={form.accounts[a.key].name} disabled={disabled} aria-label={`${a.label}の勘定科目`} onChange={(e) => setAccount(a.key, "name", e.target.value)} />
                </td>
                <td>
                  {a.key === "receivable" && form.customer_as_sub ? (
                    <span className="hint">取引先名</span>
                  ) : (
                    <input value={form.accounts[a.key].sub} disabled={disabled} aria-label={`${a.label}の補助科目`} onChange={(e) => setAccount(a.key, "sub", e.target.value)} />
                  )}
                </td>
                {bugyo && (
                  <td>
                    <input
                      value={form.accounts[a.key].code}
                      disabled={disabled}
                      inputMode="numeric"
                      aria-label={`${a.label}の科目コード`}
                      aria-invalid={!form.accounts[a.key].code}
                      onChange={(e) => setAccount(a.key, "code", e.target.value)}
                    />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>税区分{bugyo && "(消費税区分コード)"}</h3>
      <p className="hint">
        {bugyo
          ? "勘定奉行は消費税区分をコードで受け入れます。コードは勘定奉行の版によって違うので、お使いの勘定奉行の「消費税区分」の一覧にあるコードを入力してください。"
          : "会計ソフトに登録されている税区分の名前と同じにします。違う名前のときは、会計ソフトの取り込み画面で対応付けを聞かれます。"}
      </p>
      <div className="table-wrap flat">
        <table className="settings-table">
          <tbody>
            {TAXES.map((t) => (
              <tr key={t.key}>
                <th scope="row">{t.label}</th>
                <td>
                  <input
                    value={labels[t.key]}
                    disabled={disabled}
                    aria-label={`${t.label}の税区分`}
                    aria-invalid={bugyo && !labels[t.key]}
                    onChange={(e) => setLabel(t.key, e.target.value)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {!disabled && (
        <div className="actions inline">
          <button type="button" onClick={save} disabled={busy}>
            {busy ? "保存しています…" : "設定を保存"}
          </button>
          {!bugyo && (
            <button
              type="button"
              className="secondary"
              onClick={() => setForm((f) => ({ ...f, tax_labels: { ...f.tax_labels, [f.software]: { ...settings.defaults[f.software] } } }))}
            >
              税区分を初期値に戻す
            </button>
          )}
        </div>
      )}
    </div>
  );
}
