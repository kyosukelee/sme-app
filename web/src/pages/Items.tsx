import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type Item, type ItemInput, type TaxCategory } from "../api";
import { useCompany } from "../company";
import { decimal, TAX_OPTIONS } from "../format";
import { normalizeNumber } from "../lines";

const EMPTY: ItemInput = { code: null, name: "", unit: null, unit_price: "", tax_category: "standard", is_active: true };
const TAX_LABEL = Object.fromEntries(TAX_OPTIONS.map((o) => [o.value, o.label]));

/** 商品・サービスマスタ。登録した品目は、明細の品名を入力するときの候補になる */
export default function Items({ tenantId }: { tenantId: string }) {
  const { taxExempt, reloadItems } = useCompany();
  const [items, setItems] = useState<Item[] | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<Item | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(
    () => api.items(tenantId, true).then(setItems).catch((e: Error) => setError(e.message)),
    [tenantId],
  );

  useEffect(() => {
    setItems(null);
    setEditing(null);
    setError("");
    load();
  }, [load]);

  function saved() {
    setEditing(null);
    load(); // 並び順をサーバーにそろえる
    reloadItems(); // 明細の候補を最新にする
  }

  const inactiveCount = items?.filter((i) => !i.is_active).length ?? 0;
  const shown = items?.filter((i) => showInactive || i.is_active) ?? null;

  return (
    <>
      <div className="page-head">
        <h1>商品・サービス</h1>
        {items && <p className="count">{items.length - inactiveCount}件</p>}
      </div>
      <p className="lead">
        よく使う品目を登録しておくと、見積・受注・請求の明細で品名を入力するときに候補として出ます。選ぶと単位・単価・税率も入ります。
      </p>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      <div className="columns">
        <div className="list-col">
          <ItemTable items={shown} showTax={!taxExempt} selectedId={editing?.id ?? null} onSelect={setEditing} />
          {inactiveCount > 0 && (
            <label className="check inline-check">
              <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
              使用中止の品目も表示する({inactiveCount}件)
            </label>
          )}
        </div>
        <ItemForm
          key={`${tenantId}:${editing?.id ?? "new"}`}
          tenantId={tenantId}
          item={editing}
          showTax={!taxExempt}
          onSaved={saved}
          onCancel={() => setEditing(null)}
        />
      </div>
    </>
  );
}

function ItemTable({
  items,
  showTax,
  selectedId,
  onSelect,
}: {
  items: Item[] | null;
  showTax: boolean;
  selectedId: string | null;
  onSelect: (i: Item) => void;
}) {
  if (items === null) return <p className="placeholder">読み込み中…</p>;
  if (items.length === 0) {
    return <p className="placeholder">商品・サービスはまだありません。右のフォームから追加してください。</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">コード</th>
            <th scope="col">品名</th>
            <th scope="col" className="num">
              単価(税抜)
            </th>
            <th scope="col">単位</th>
            {showTax && <th scope="col">税率</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id} className={[i.id === selectedId && "is-selected", !i.is_active && "is-inactive"].filter(Boolean).join(" ") || undefined}>
              <td>{i.code ?? "―"}</td>
              <td>
                <button type="button" className="link-button" onClick={() => onSelect(i)} aria-label={`${i.name}を編集`}>
                  {i.name}
                </button>
                {!i.is_active && <span className="badge inline is-lost">使用中止</span>}
              </td>
              <td className="num">{decimal(i.unit_price)}</td>
              <td>{i.unit ?? "―"}</td>
              {showTax && <td>{TAX_LABEL[i.tax_category]}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ItemForm({
  tenantId,
  item,
  showTax,
  onSaved,
  onCancel,
}: {
  tenantId: string;
  item: Item | null; // 編集するとき。新規なら null
  showTax: boolean;
  onSaved: (i: Item) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<ItemInput>(() =>
    item ? { ...item, unit_price: item.unit_price.replace(/\.00$/, "") } : EMPTY,
  );
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  const text = (key: "code" | "name" | "unit" | "unit_price") => ({
    value: form[key] ?? "",
    onChange: (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value })),
  });

  const price = normalizeNumber(form.unit_price);
  const priceInvalid = form.unit_price.trim() !== "" && !/^-?\d+(\.\d{1,2})?$/.test(price);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!/^-?\d+(\.\d{1,2})?$/.test(price)) {
      setMessage({ kind: "error", text: "単価は数字で入力してください(小数は2桁まで)。" });
      return;
    }
    setSaving(true);
    setMessage(null);
    const input: ItemInput = {
      ...form,
      code: form.code?.trim() || null,
      name: form.name.trim(),
      unit: form.unit?.trim() || null,
      unit_price: price,
    };
    try {
      const result = item ? await api.updateItem(tenantId, item.id, input) : await api.createItem(tenantId, input);
      onSaved(result);
      if (!item) {
        setForm(EMPTY);
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
      <h2>{item ? "商品・サービスを編集" : "商品・サービスを追加"}</h2>
      <label>
        コード(任意)
        <input {...text("code")} placeholder="例: WEB-01" />
      </label>
      <label>
        品名
        <input required {...text("name")} placeholder="例: ホームページ保守" />
      </label>
      <div className="field-pair">
        <label>
          単価(税抜)
          <input
            required
            className="num"
            inputMode="decimal"
            aria-invalid={priceInvalid}
            {...text("unit_price")}
            onBlur={() => setForm((f) => ({ ...f, unit_price: normalizeNumber(f.unit_price) }))}
          />
        </label>
        <label>
          単位
          <input {...text("unit")} placeholder="例: 月、式、個" />
        </label>
      </div>
      {showTax && (
        <label>
          税率
          <select value={form.tax_category} onChange={(e) => setForm((f) => ({ ...f, tax_category: e.target.value as TaxCategory }))}>
            {TAX_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {item && (
        <label className="check">
          <input type="checkbox" checked={!form.is_active} onChange={(e) => setForm((f) => ({ ...f, is_active: !e.target.checked }))} />
          使用中止にする
          <span className="hint">明細の候補に出なくなります。作成済みの書類には影響しません。</span>
        </label>
      )}
      {item && <p className="hint">単価を変えても、作成済みの見積・受注・請求の明細は変わりません。</p>}

      <div className="actions">
        <button type="submit" disabled={saving}>
          {saving ? "保存しています…" : item ? "変更を保存" : "商品・サービスを追加"}
        </button>
        {item && (
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
