import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, type FormField, type RequestDetail, type RequestType, type RoutePreview } from "../../api";
import { yen } from "../../format";
import { navigate } from "../../router";
import { fileSize, readUpload, RouteView } from "./common";

/** 承認の段の要約(例: 上長 → 代表(10万円以上)) */
export function routeSummary(t: RequestType): string {
  return t.steps.map((s) => (s.min_amount ? `${s.label}(${yen(s.min_amount)}以上)` : s.label)).join(" → ");
}

/**
 * 申請の作成・編集(下書きと、差し戻された申請)。
 * 種別を選ぶと入力項目が並ぶ。右側(スマホでは下)に、申請するとどの承認ルートになるかを表示する
 */
export default function RequestEditor({ tenantId, id, initialType }: { tenantId: string; id?: string; initialType?: string | null }) {
  const [types, setTypes] = useState<RequestType[] | null>(null);
  const [request, setRequest] = useState<RequestDetail | null>(null);
  const [typeId, setTypeId] = useState("");
  const [title, setTitle] = useState("");
  const [form, setForm] = useState<Record<string, string>>({});
  const [amount, setAmount] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [preview, setPreview] = useState<RoutePreview | null>(null);
  const [savedId, setSavedId] = useState(id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.requestTypes(tenantId), id ? api.request(tenantId, id) : Promise.resolve(null)])
      .then(([list, r]) => {
        setTypes(list);
        if (r) {
          if (!r.permissions.can_edit) {
            navigate(`/requests/${r.id}`); // 承認待ち・完了した申請は直せない
            return;
          }
          setRequest(r);
          setTypeId(r.request_type_id);
          setTitle(r.title);
          setForm(r.form_data);
          setAmount(r.amount ?? "");
        } else if (initialType) {
          const t = list.find((x) => x.code === initialType || x.id === initialType);
          if (t) setTypeId(t.id);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [tenantId, id, initialType]);

  const type = useMemo(() => types?.find((t) => t.id === typeId) ?? null, [types, typeId]);
  const amountValue = amount.replace(/[,，円\s]/g, "") || null;

  // 承認ルートの見込み(金額の条件で段が変わるので、入力が落ち着いてから問い合わせる)
  useEffect(() => {
    if (!typeId) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      api
        .routePreview(tenantId, typeId, amountValue && /^\d+$/.test(amountValue) ? amountValue : null, ctrl.signal)
        .then(setPreview)
        .catch(() => {});
    }, 300);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [tenantId, typeId, amountValue]);

  if (error && !types) {
    return (
      <p className="alert" role="alert">
        {error}
      </p>
    );
  }
  if (!types) return <p className="placeholder">読み込み中…</p>;

  const returnedReason = request?.status === "returned" ? [...request.events].reverse().find((e) => e.action === "return") : null;

  if (!type) {
    return (
      <>
        <div className="page-head">
          <a className="back" href="#/requests">
            申請・承認の一覧
          </a>
          <h1>申請を作成</h1>
        </div>
        <p className="lead">申請の種類を選んでください。</p>
        <ul className="type-picker">
          {types.map((t) => (
            <li key={t.id}>
              <button type="button" className="type-card" onClick={() => setTypeId(t.id)}>
                <strong>{t.name}</strong>
                {t.description && <span>{t.description}</span>}
                <small>承認: {routeSummary(t)}</small>
              </button>
            </li>
          ))}
        </ul>
      </>
    );
  }

  async function save(submit: boolean) {
    if (!type) return;
    setBusy(true);
    setError("");
    try {
      const input = { request_type_id: type.id, title, form_data: form, amount: type.amount_mode === "none" ? null : amountValue };
      let detail = savedId ? await api.updateRequest(tenantId, savedId, input) : await api.createRequest(tenantId, input);
      setSavedId(detail.id);
      for (const f of files) {
        detail = await api.addAttachment(tenantId, detail.id, await readUpload(f));
        setFiles((list) => list.filter((x) => x !== f));
      }
      setRequest(detail);
      if (submit) detail = await api.requestAction(tenantId, detail.id, "submit");
      navigate(`/requests/${detail.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void save(true);
  }

  async function removeAttachment(attachmentId: string) {
    if (!request) return;
    try {
      setRequest(await api.deleteAttachment(tenantId, request.id, attachmentId));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function removeDraft() {
    if (!request || !window.confirm("この下書きを削除しますか?")) return;
    try {
      await api.deleteRequest(tenantId, request.id);
      navigate("/requests?box=mine");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const returned = request?.status === "returned";

  return (
    <>
      <div className="page-head">
        <a className="back" href={request ? `#/requests/${request.id}` : "#/requests"}>
          {request ? "申請の詳細" : "申請・承認の一覧"}
        </a>
        <h1>{returned ? "差し戻された申請を直す" : request ? "下書きを編集" : `${type.name}を作成`}</h1>
        {request?.request_number && <p className="count">{request.request_number}</p>}
      </div>

      {returnedReason && (
        <div className="notice-warn return-reason" role="note">
          <strong>{returnedReason.actor?.display_name}さんから差し戻されました</strong>
          <p>{returnedReason.comment}</p>
        </div>
      )}

      <form className="editor" onSubmit={onSubmit}>
        <div className="editor-main">
          <section className="panel">
            <div className="field-row">
              <label>
                申請の種類
                <select value={type.id} onChange={(e) => setTypeId(e.target.value)} disabled={returned}>
                  {types.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
                {type.description && <span className="hint">{type.description}</span>}
              </label>
              {type.amount_mode !== "none" && (
                <label>
                  {type.amount_label}(円){type.amount_mode === "optional" && "(任意)"}
                  <input
                    inputMode="numeric"
                    className="num"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    placeholder="例: 150000"
                    required={type.amount_mode === "required"}
                  />
                </label>
              )}
            </div>
            <label>
              件名
              <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={100} placeholder={placeholderTitle(type.code)} />
            </label>
            <div className="form-fields">
              {type.form_schema.map((f) => (
                <Field key={f.key} field={f} value={form[f.key] ?? ""} onChange={(v) => setForm((x) => ({ ...x, [f.key]: v }))} />
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>添付(領収書・見積書など)</h2>
            <p className="hint">画像(JPEG・PNG など)とPDFを、1つ10MBまで添付できます。スマホではカメラで撮った写真も選べます。</p>
            {(request?.attachments.length ?? 0) + files.length > 0 && (
              <ul className="attachment-list">
                {request?.attachments.map((a) => (
                  <li key={a.id}>
                    <span>{a.file_name}</span>
                    <small>{fileSize(a.size_bytes)}</small>
                    <button type="button" className="link-button" onClick={() => removeAttachment(a.id)}>
                      外す
                    </button>
                  </li>
                ))}
                {files.map((f, i) => (
                  <li key={`${f.name}-${i}`}>
                    <span>{f.name}</span>
                    <small>{fileSize(f.size)}・保存すると添付されます</small>
                    <button type="button" className="link-button" onClick={() => setFiles((list) => list.filter((x) => x !== f))}>
                      外す
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <label className="file-pick">
              <span className="visually-hidden">ファイルを選ぶ</span>
              <input
                type="file"
                accept="image/*,application/pdf"
                multiple
                onChange={(e) => {
                  const picked = Array.from(e.target.files ?? []);
                  setFiles((list) => [...list, ...picked]);
                  e.target.value = "";
                }}
              />
            </label>
          </section>
        </div>

        <aside className="editor-side stack">
          <section className="panel">
            <h2>承認ルート</h2>
            {preview?.error ? (
              <p className="alert" role="alert">
                {preview.error}
              </p>
            ) : preview ? (
              <>
                <RouteView steps={preview.steps} applicant="あなた" preview />
                {preview.auto_approve && <p className="hint">承認する人がいないため、申請と同時に承認済になります。</p>}
                {type.steps.some((s) => s.min_amount) && (
                  <p className="hint">金額によって承認する人が変わります(承認: {routeSummary(type)})。</p>
                )}
              </>
            ) : (
              <p className="placeholder">確認しています…</p>
            )}
          </section>
          {error && (
            <p className="alert" role="alert">
              {error}
            </p>
          )}
          <div className="actions">
            <button type="submit" disabled={busy || !!preview?.error}>
              {busy ? "保存しています…" : returned ? "直して再申請する" : "申請する"}
            </button>
            <button type="button" className="secondary" onClick={() => save(false)} disabled={busy}>
              {returned ? "保存だけする" : "下書きに保存"}
            </button>
            {request?.permissions.can_delete && (
              <button type="button" className="danger-text" onClick={removeDraft} disabled={busy}>
                下書きを削除
              </button>
            )}
          </div>
        </aside>
      </form>
    </>
  );
}

function placeholderTitle(code: string): string {
  return (
    { expense: "例: 11月 客先訪問の交通費", purchase: "例: 営業用ノートPCの購入", leave: "例: 12月1日 有給休暇", general: "例: 展示会への出展" }[
      code
    ] ?? ""
  );
}

function Field({ field, value, onChange }: { field: FormField; value: string; onChange: (v: string) => void }) {
  const label = (
    <>
      {field.label}
      {!field.required && "(任意)"}
    </>
  );
  const wide = field.type === "textarea";
  if (field.type === "select") {
    return (
      <label>
        {label}
        <select value={value} onChange={(e) => onChange(e.target.value)} required={field.required}>
          <option value="">{field.required ? "選んでください" : "なし"}</option>
          {field.options?.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (wide) {
    return (
      <label className="wide">
        {label}
        <textarea value={value} onChange={(e) => onChange(e.target.value)} required={field.required} maxLength={field.max_length} rows={4} />
      </label>
    );
  }
  return (
    <label>
      {label}
      <input
        type={field.type === "date" ? "date" : "text"}
        inputMode={field.type === "number" ? "decimal" : undefined}
        className={field.type === "number" ? "num" : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={field.required}
        maxLength={field.max_length}
      />
    </label>
  );
}
