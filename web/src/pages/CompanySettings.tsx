import { useEffect, useState, type FormEvent } from "react";
import { api, type CompanyProfile, type PaymentTerms } from "../api";
import { useCompany } from "../company";
import TermsFields from "../components/TermsFields";

const EMPTY_PROFILE: CompanyProfile = {
  business_type: "corporation",
  name: "",
  representative_name: null,
  tax_exempt: false,
  postal_code: null,
  address: null,
  phone: null,
  registration_number: null,
  bank_account: null,
};

/** 自社情報(書類に印字する内容)と、請求の締め日・支払日の初期設定 */
export default function CompanySettings({ tenantId }: { tenantId: string }) {
  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [terms, setTerms] = useState<PaymentTerms | null>(null);
  const [feeLimit, setFeeLimit] = useState("880");
  const [canEdit, setCanEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);
  const company = useCompany();

  useEffect(() => {
    setTerms(null);
    api
      .company(tenantId)
      .then((c) => {
        setProfile(c.profile ?? EMPTY_PROFILE);
        setTerms(c.terms);
        setFeeLimit(String(c.bank_fee_limit));
        setCanEdit(c.can_edit);
      })
      .catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  type TextKey = "name" | "representative_name" | "postal_code" | "address" | "phone" | "registration_number" | "bank_account";
  const field = (key: TextKey) => ({
    value: profile[key] ?? "",
    disabled: !canEdit,
    onChange: (e: { target: { value: string } }) => setProfile((p) => ({ ...p, [key]: e.target.value || null })),
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!terms) return;
    const limit = feeLimit.normalize("NFKC").replace(/,/g, "").trim();
    if (!/^\d+$/.test(limit) || Number(limit) > 10000) {
      setMessage({ kind: "error", text: "振込手数料の上限は、0〜10,000円の数字で入力してください。" });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const saved = await api.updateCompany(tenantId, {
        profile: { ...profile, name: profile.name.trim() },
        terms,
        bank_fee_limit: Number(limit),
      });
      setProfile(saved.profile ?? EMPTY_PROFILE);
      setTerms(saved.terms);
      setFeeLimit(String(saved.bank_fee_limit));
      company.reload(); // 消費税の欄・源泉徴収の欄の表示を切り替える
      setMessage({ kind: "done", text: "自社情報を保存しました。これから発行する書類に反映されます。" });
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  if (!terms) {
    return message ? (
      <p className="alert" role="alert">
        {message.text}
      </p>
    ) : (
      <p className="placeholder">読み込み中…</p>
    );
  }

  return (
    <>
      <div className="page-head">
        <h1>自社情報</h1>
      </div>
      {!canEdit && <p className="notice page-note">自社情報を変更できるのは管理者だけです。</p>}

      <form className="settings" onSubmit={submit}>
        <section className="panel">
          <h2>事業形態と消費税</h2>
          <div className="choice-row">
            <fieldset className="choices" disabled={!canEdit}>
              <legend>事業形態</legend>
              <label>
                <input
                  type="radio"
                  name="business_type"
                  checked={profile.business_type === "corporation"}
                  onChange={() => setProfile((p) => ({ ...p, business_type: "corporation" }))}
                />
                法人
              </label>
              <label>
                <input
                  type="radio"
                  name="business_type"
                  checked={profile.business_type === "sole_proprietor"}
                  onChange={() => setProfile((p) => ({ ...p, business_type: "sole_proprietor" }))}
                />
                個人事業主
              </label>
            </fieldset>
            <fieldset className="choices" disabled={!canEdit}>
              <legend>消費税</legend>
              <label>
                <input
                  type="radio"
                  name="tax_exempt"
                  checked={!profile.tax_exempt}
                  onChange={() => setProfile((p) => ({ ...p, tax_exempt: false }))}
                />
                課税事業者(消費税を請求する)
              </label>
              <label>
                <input
                  type="radio"
                  name="tax_exempt"
                  checked={profile.tax_exempt}
                  onChange={() => setProfile((p) => ({ ...p, tax_exempt: true, registration_number: null }))}
                />
                免税事業者(消費税を表示しない)
              </label>
            </fieldset>
          </div>
          <p className="hint">
            {profile.tax_exempt
              ? "見積書・注文請書・請求書に消費税を表示せず、入力した金額をそのまま請求します。適格請求書(インボイス)は発行できないため、登録番号は空欄になります。"
              : "登録番号があれば、請求書は適格請求書(インボイス)として発行します。"}
            {profile.business_type === "sole_proprietor" &&
              " 個人事業主は、取引先や請求書ごとに源泉徴収税額を差し引いて請求できます。"}
          </p>
        </section>

        <section className="panel">
          <h2>書類に印字する内容</h2>
          <div className="field-row">
            <label>
              {profile.business_type === "sole_proprietor" ? "屋号(なければ氏名)" : "会社名"}
              <input required {...field("name")} placeholder={profile.business_type === "sole_proprietor" ? "例: やまだデザイン室" : ""} />
            </label>
            <label>
              {profile.business_type === "sole_proprietor" ? "氏名(屋号の下に印字)" : "代表者(任意)"}
              <input
                {...field("representative_name")}
                placeholder={profile.business_type === "sole_proprietor" ? "例: 山田 太郎" : "例: 代表取締役 山田 太郎"}
              />
            </label>
            <label>
              登録番号(インボイス)
              <input {...field("registration_number")} placeholder="T1234567890123" disabled={!canEdit || profile.tax_exempt} />
              <span className="hint">
                {profile.tax_exempt
                  ? "免税事業者は登録番号を持てません。"
                  : "適格請求書発行事業者の登録番号です。登録していない場合は空欄にしてください。"}
              </span>
            </label>
            <label>
              郵便番号
              <input {...field("postal_code")} placeholder="730-0011" />
            </label>
            <label>
              電話番号
              <input {...field("phone")} />
            </label>
          </div>
          <label>
            住所
            <input {...field("address")} />
          </label>
          <label>
            お振込先(請求書に印字されます)
            <textarea rows={3} {...field("bank_account")} placeholder={"〇〇銀行 本店営業部\n普通預金 1234567\n口座名義 カ)〇〇"} />
          </label>
        </section>

        <section className="panel">
          <h2>請求の締め日・支払日</h2>
          <p className="hint">取引先ごとに設定していない場合は、この条件で請求書のお支払期限を決めます。</p>
          <TermsFields terms={terms} onChange={setTerms} disabled={!canEdit} />
        </section>

        <section className="panel">
          <h2>入金消込</h2>
          <label className="narrow">
            振込手数料として消し込む差額の上限(円)
            <input
              className="num"
              inputMode="numeric"
              value={feeLimit}
              disabled={!canEdit}
              onChange={(e) => setFeeLimit(e.target.value)}
            />
          </label>
          <p className="hint">
            取引先が振込手数料を差し引いて振り込んだとき、請求額との差額がこの金額以内なら「振込手数料」として請求書を入金済みにできます。0
            にすると、差額は一部入金として残します。
          </p>
        </section>

        {canEdit && (
          <div className="settings-actions">
            <button type="submit" disabled={busy}>
              {busy ? "保存しています…" : "保存する"}
            </button>
            {message && (
              <p className={message.kind === "done" ? "notice" : "alert"} role="status">
                {message.text}
              </p>
            )}
          </div>
        )}
      </form>

      {canEdit && <ExportPanel tenantId={tenantId} />}
    </>
  );
}

/** データ一式のダウンロード(解約・乗り換えのときの持ち出し用。管理者だけ) */
function ExportPanel({ tenantId }: { tenantId: string }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  async function download() {
    setBusy(true);
    setMessage(null);
    try {
      await api.downloadExport(tenantId);
      setMessage({ kind: "done", text: "ダウンロードしました。ZIPファイルを保存しておいてください。" });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel export-panel">
      <h2>データの書き出し</h2>
      <p>
        取引先・商品・見積・受注・請求・入金・変更の履歴をすべて CSV にし、発行した書類の控えPDFといっしょに1つの ZIP
        ファイルにまとめてダウンロードします。解約やほかのサービスへの乗り換えのときに使います。
      </p>
      <p className="hint">
        控えPDFには発行時のハッシュ値を添えています。電子取引の書類は原則7年間の保存が必要なので、解約後もファイルを保存してください。
      </p>
      <div className="actions inline">
        <button type="button" className="secondary" onClick={download} disabled={busy}>
          {busy ? "書き出しています…" : "データ一式をダウンロード"}
        </button>
      </div>
      {message && (
        <p className={message.kind === "done" ? "notice" : "alert"} role="status">
          {message.text}
        </p>
      )}
    </section>
  );
}
