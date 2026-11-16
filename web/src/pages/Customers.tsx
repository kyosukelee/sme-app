import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type Customer, type CustomerInput, type PaymentTerms } from "../api";
import { useCompany } from "../company";
import TermsFields, { termsLabel } from "../components/TermsFields";

const EMPTY: CustomerInput = {
  name: "",
  honorific: "御中",
  contact_name: null,
  address: null,
  email: null,
  withholding_default: false,
  terms: null,
  is_active: true,
};

export default function Customers({ tenantId }: { tenantId: string }) {
  const { soleProprietor } = useCompany();
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [companyTerms, setCompanyTerms] = useState<PaymentTerms | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(
    () => api.customers(tenantId, true).then(setCustomers).catch((e: Error) => setError(e.message)),
    [tenantId],
  );

  useEffect(() => {
    setCustomers(null);
    setEditing(null);
    setError("");
    load();
    api.company(tenantId).then((c) => setCompanyTerms(c.terms)).catch(() => setCompanyTerms(null));
  }, [tenantId, load]);

  function saved() {
    setEditing(null);
    load(); // 並び順をサーバーにそろえる
  }

  const inactiveCount = customers?.filter((c) => !c.is_active).length ?? 0;
  const shown = customers?.filter((c) => showInactive || c.is_active) ?? null;

  return (
    <>
      <div className="page-head">
        <h1>取引先</h1>
        {customers && <p className="count">{customers.length - inactiveCount}件</p>}
      </div>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      <div className="columns">
        <div className="list-col">
          <CustomerTable
            customers={shown}
            showWithholding={soleProprietor}
            companyTerms={companyTerms}
            selectedId={editing?.id ?? null}
            onSelect={setEditing}
          />
          {inactiveCount > 0 && (
            <label className="check inline-check">
              <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
              使用中止の取引先も表示する({inactiveCount}件)
            </label>
          )}
        </div>
        <CustomerForm
          key={`${tenantId}:${editing?.id ?? "new"}`}
          tenantId={tenantId}
          customer={editing}
          companyTerms={companyTerms}
          onSaved={saved}
          onCancel={() => setEditing(null)}
        />
      </div>
    </>
  );
}

function CustomerTable({
  customers,
  showWithholding,
  companyTerms,
  selectedId,
  onSelect,
}: {
  customers: Customer[] | null;
  showWithholding: boolean;
  companyTerms: PaymentTerms | null;
  selectedId: string | null;
  onSelect: (c: Customer) => void;
}) {
  if (customers === null) return <p className="placeholder">読み込み中…</p>;
  if (customers.length === 0) {
    return <p className="placeholder">取引先はまだありません。右のフォームから最初の1件を追加してください。</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">名称</th>
            <th scope="col">担当者</th>
            <th scope="col">締め・支払</th>
            {showWithholding && <th scope="col">源泉徴収</th>}
          </tr>
        </thead>
        <tbody>
          {customers.map((c) => (
            <tr key={c.id} className={[c.id === selectedId && "is-selected", !c.is_active && "is-inactive"].filter(Boolean).join(" ") || undefined}>
              <td>
                <button type="button" className="link-button" onClick={() => onSelect(c)} aria-label={`${c.name}を編集`}>
                  {c.name}
                </button>
                <span className="honorific">{c.honorific}</span>
                {!c.is_active && <span className="badge inline is-lost">使用中止</span>}
              </td>
              <td>{c.contact_name ?? "―"}</td>
              <td>
                {c.terms ? (
                  termsLabel(c.terms)
                ) : (
                  <span className="muted">会社の設定{companyTerms ? `(${termsLabel(companyTerms)})` : ""}</span>
                )}
              </td>
              {showWithholding && <td>{c.withholding_default ? "する" : "―"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CustomerForm({
  tenantId,
  customer,
  companyTerms,
  onSaved,
  onCancel,
}: {
  tenantId: string;
  customer: Customer | null; // 編集するとき。新規なら null
  companyTerms: PaymentTerms | null;
  onSaved: (c: Customer) => void;
  onCancel: () => void;
}) {
  const { soleProprietor } = useCompany();
  const [form, setForm] = useState<CustomerInput>(() => (customer ? { ...customer } : EMPTY));
  // 取引先ごとの条件を使うかどうか。使うときの初期値は会社の設定
  const [ownTerms, setOwnTerms] = useState(customer?.terms != null);
  const [terms, setTerms] = useState<PaymentTerms>(
    customer?.terms ?? companyTerms ?? { closing_day: 31, payment_month_offset: 1, payment_day: 31 },
  );
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  const text = (key: "name" | "contact_name" | "address" | "email") => ({
    value: form[key] ?? "",
    onChange: (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value })),
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    const input: CustomerInput = {
      ...form,
      name: form.name.trim(),
      contact_name: form.contact_name?.trim() || null,
      address: form.address?.trim() || null,
      email: form.email?.trim() || null,
      withholding_default: soleProprietor ? form.withholding_default : (customer?.withholding_default ?? false),
      terms: ownTerms ? terms : null,
    };
    try {
      const result = customer ? await api.updateCustomer(tenantId, customer.id, input) : await api.createCustomer(tenantId, input);
      onSaved(result);
      if (!customer) {
        setForm(EMPTY);
        setOwnTerms(false);
        setMessage({ kind: "done", text: `「${result.name}」を追加しました` });
      }
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>{customer ? "取引先を編集" : "取引先を追加"}</h2>
      <label>
        名称
        <input required {...text("name")} placeholder="株式会社〇〇" />
      </label>
      <label>
        敬称
        <select value={form.honorific} onChange={(e) => setForm((f) => ({ ...f, honorific: e.target.value }))}>
          <option>御中</option>
          <option>様</option>
        </select>
      </label>
      <label>
        担当者
        <input {...text("contact_name")} />
      </label>
      <label>
        住所
        <input {...text("address")} />
      </label>
      <label>
        メールアドレス
        <input type="email" {...text("email")} />
      </label>

      <fieldset className="choices">
        <legend>締め日・支払日</legend>
        <label>
          <input type="radio" name="terms" checked={!ownTerms} onChange={() => setOwnTerms(false)} />
          会社の設定に従う{companyTerms ? `(${termsLabel(companyTerms)})` : ""}
        </label>
        <label>
          <input type="radio" name="terms" checked={ownTerms} onChange={() => setOwnTerms(true)} />
          この取引先だけの条件にする
        </label>
      </fieldset>
      {ownTerms && (
        <div className="own-terms">
          <TermsFields terms={terms} onChange={setTerms} />
        </div>
      )}

      {soleProprietor && (
        <label className="check">
          <input
            type="checkbox"
            checked={form.withholding_default}
            onChange={(e) => setForm((f) => ({ ...f, withholding_default: e.target.checked }))}
          />
          請求書で源泉徴収税額を差し引く
          <span className="hint">デザイン料・原稿料・講演料などを法人に請求するときに選びます。請求書ごとに変えることもできます。</span>
        </label>
      )}

      {customer && (
        <label className="check">
          <input
            type="checkbox"
            checked={!form.is_active}
            onChange={(e) => setForm((f) => ({ ...f, is_active: !e.target.checked }))}
          />
          使用中止にする
          <span className="hint">見積・受注・請求の取引先の選択肢に出なくなります。作成済みの書類はそのまま残ります。</span>
        </label>
      )}

      {customer && (
        <p className="hint">名称や住所を変えても、発行済みの書類の宛名は発行したときのままです。締め日・支払日は、これから作る請求書から使われます。</p>
      )}

      <div className="actions">
        <button type="submit" disabled={saving}>
          {saving ? "保存しています…" : customer ? "変更を保存" : "取引先を追加"}
        </button>
        {customer && (
          <button type="button" className="secondary" onClick={onCancel} disabled={saving}>
            やめる
          </button>
        )}
      </div>
      {message && (
        <p className={message.kind === "done" ? "notice" : "alert"} role="status">
          {message.text}
        </p>
      )}
    </form>
  );
}
