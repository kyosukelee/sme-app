import { useEffect, useState, type FormEvent } from "react";
import { api, type DocType, type DocumentQuery, type IssuedDocument } from "../api";
import { jdate, jdatetime, yen } from "../format";

const DOC_LABELS: Record<DocType, string> = { quote: "見積書", order: "注文請書", invoice: "請求書" };
const DOC_LINKS: Record<DocType, string> = { quote: "#/quotes/", order: "#/orders/", invoice: "#/invoices/" };

const EMPTY: DocumentQuery = {};

/**
 * 発行済み書類の検索(電子帳簿保存法の検索要件)。
 * 発行時に保存した控えを、取引年月日・取引金額・取引先で検索する。日付と金額は範囲で、条件は組み合わせて指定できる
 */
export default function DocumentSearch({ tenantId }: { tenantId: string }) {
  const [form, setForm] = useState<DocumentQuery>(EMPTY);
  const [results, setResults] = useState<IssuedDocument[] | null>(null);
  const [checks, setChecks] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(query: DocumentQuery) {
    setBusy(true);
    setError("");
    try {
      setResults(await api.searchDocuments(tenantId, normalize(query)));
      setChecks({});
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void run(EMPTY);
    // 会社を切り替えたら、最初から検索し直す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  const field = (key: keyof DocumentQuery) => ({
    value: (form[key] as string | undefined) ?? "",
    onChange: (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value || undefined })),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    void run(form);
  }

  function clear() {
    setForm(EMPTY);
    void run(EMPTY);
  }

  async function verify(doc: IssuedDocument) {
    try {
      const r = await api.verifyDocument(tenantId, doc.id);
      setChecks((c) => ({ ...c, [doc.id]: r }));
    } catch (e) {
      setChecks((c) => ({ ...c, [doc.id]: { ok: false, message: (e as Error).message } }));
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>書類の検索</h1>
        {results && <p className="count">{results.length}件{results.length === 500 && "(上限)"}</p>}
      </div>
      <p className="hint page-note">
        発行時に保存した見積書・注文請書・請求書の控えを検索します。取引年月日・取引金額は範囲で指定でき、条件は組み合わせて使えます(電子帳簿保存法の検索要件)。
        取引年月日は書類の発行日です。
      </p>

      <form className="panel search-form" onSubmit={submit}>
        <label>
          書類
          <select {...field("doc_type")}>
            <option value="">すべて</option>
            <option value="quote">見積書</option>
            <option value="order">注文請書</option>
            <option value="invoice">請求書</option>
          </select>
        </label>
        <fieldset>
          <legend>取引年月日</legend>
          <input type="date" aria-label="取引年月日(から)" {...field("date_from")} />
          <span>〜</span>
          <input type="date" aria-label="取引年月日(まで)" {...field("date_to")} />
        </fieldset>
        <fieldset>
          <legend>取引金額(税込・円)</legend>
          <input className="num" inputMode="numeric" aria-label="取引金額(以上)" placeholder="下限" {...field("amount_min")} />
          <span>〜</span>
          <input className="num" inputMode="numeric" aria-label="取引金額(以下)" placeholder="上限" {...field("amount_max")} />
        </fieldset>
        <label>
          取引先
          <input placeholder="名前の一部" {...field("customer")} />
        </label>
        <label>
          書類番号
          <input placeholder="例: INV-2026" {...field("number")} />
        </label>
        <div className="search-actions">
          <button type="submit" disabled={busy}>
            {busy ? "検索しています…" : "検索"}
          </button>
          <button type="button" className="secondary" onClick={clear} disabled={busy}>
            条件をクリア
          </button>
        </div>
      </form>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {results === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : results.length === 0 ? (
        <p className="placeholder">条件に合う控えはありません。</p>
      ) : (
        <div className="table-wrap">
          <table className="nowrap">
            <thead>
              <tr>
                <th scope="col">取引年月日</th>
                <th scope="col">書類</th>
                <th scope="col">番号</th>
                <th scope="col">取引先</th>
                <th scope="col" className="num">
                  取引金額
                </th>
                <th scope="col">控え</th>
              </tr>
            </thead>
            <tbody>
              {results.map((d) => {
                const check = checks[d.id];
                return (
                  <tr key={d.id}>
                    <td>{jdate(d.transaction_date)}</td>
                    <td>{DOC_LABELS[d.doc_type]}</td>
                    <td>
                      <a href={`${DOC_LINKS[d.doc_type]}${d.document_id}`}>{d.document_number}</a>
                      {d.version > 1 && <span className="honorific">第{d.version}版</span>}
                      {d.superseded && <span className="badge is-lost inline">訂正済</span>}
                    </td>
                    <td>{d.customer_name}</td>
                    <td className="num">{yen(d.total_amount)}</td>
                    <td>
                      <button type="button" className="link-button" onClick={() => api.openDocumentPdf(tenantId, d.id).catch((e) => setError(e.message))}>
                        開く
                      </button>
                      <span className="sep">|</span>
                      {check ? (
                        <span className={check.ok ? "verify-ok" : "verify-ng"} title={check.message}>
                          {check.ok ? "改ざんなし" : "要確認"}
                        </span>
                      ) : (
                        <button type="button" className="link-button" onClick={() => verify(d)} title={`${jdatetime(d.issued_at)} 発行時の控えと照合します`}>
                          改ざんチェック
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {Object.values(checks).some((c) => !c.ok) && (
        <p className="alert">
          発行時の控えと一致しない書類があります。ファイルが書き換えられた可能性があります。管理者に確認してください。
        </p>
      )}
    </>
  );
}

/** 金額欄の全角数字・桁区切りを取り除く */
function normalize(query: DocumentQuery): DocumentQuery {
  const digits = (v?: string) =>
    v ? v.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)).replace(/[,，\s円]/g, "") || undefined : undefined;
  return { ...query, amount_min: digits(query.amount_min), amount_max: digits(query.amount_max) };
}
