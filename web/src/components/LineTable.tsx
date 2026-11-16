import { useId } from "react";
import type { Item } from "../api";
import { useCompany } from "../company";
import { decimal, TAX_OPTIONS, yen } from "../format";
import { blankRow, isBlank, normalizeNumber, rowIssues, type Row } from "../lines";

type Props = {
  rows: Row[];
  onChange: (update: (rows: Row[]) => Row[]) => void;
  amountByKey: Map<number, string>;
  showErrors: boolean;
  withDeliveryDate?: boolean; // 受注では各行の納品日、請求書では取引日を入力する
  dateLabel?: string;
  dateRequired?: boolean;
  readOnly?: boolean;
  showTax?: boolean; // 免税事業者は税率の欄を出さない
};

/** 明細の入力表。行の追加・削除と、数量・単価の全角→半角の整形までを受け持つ */
export default function LineTable({
  rows,
  onChange,
  amountByKey,
  showErrors,
  withDeliveryDate = false,
  dateLabel = "納品日",
  dateRequired = false,
  readOnly = false,
  showTax = true,
}: Props) {
  const { items } = useCompany();
  const listId = useId();

  /** 品名に商品・サービスの名前を選んだら、単位・単価・税率も入れる(数量・単価を変えられない行は品名だけ) */
  const changeDescription = (key: number, value: string, fillPrice: boolean) => {
    const item: Item | undefined = items.find((it) => it.name === value);
    onChange((list) =>
      list.map((r) => {
        if (r.key !== key) return r;
        if (!item || r.description === value) return { ...r, description: value };
        return {
          ...r,
          description: value,
          unit: item.unit ?? "",
          ...(fillPrice ? { unit_price: item.unit_price.replace(/\.00$/, ""), tax_category: item.tax_category } : {}),
        };
      }),
    );
  };

  const update = (key: number, field: "description" | "quantity" | "unit" | "unit_price" | "tax_category" | "delivered_on", value: string) =>
    onChange((list) =>
      list.map((r) => (r.key === key ? { ...r, [field]: value, ...(field === "delivered_on" ? { date_confirmed: true } : {}) } : r)),
    );

  /** 数量・単価の欄を離れたら、全角の数字などを半角にそろえる */
  const tidy = (key: number, field: "quantity" | "unit_price") =>
    onChange((list) => list.map((r) => (r.key === key ? { ...r, [field]: normalizeNumber(r[field]) } : r)));

  return (
    <>
      <div className="table-wrap flat">
        <table className={`lines${withDeliveryDate ? " with-date" : ""}`}>
          <thead>
            <tr>
              <th scope="col" className="col-no">
                行
              </th>
              <th scope="col">品名</th>
              <th scope="col" className="num col-qty">
                数量
              </th>
              <th scope="col" className="col-unit">
                単位
              </th>
              <th scope="col" className="num col-price">
                単価(税抜)
              </th>
              {showTax && (
                <th scope="col" className="col-tax">
                  税率
                </th>
              )}
              {withDeliveryDate && (
                <th scope="col" className="col-date">
                  {dateLabel}
                </th>
              )}
              <th scope="col" className="num col-amount">
                金額
              </th>
              {!readOnly && (
                <th scope="col" className="col-remove">
                  <span className="visually-hidden">削除</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const issues = showErrors && !isBlank(row) ? rowIssues(row) : null;
              const n = i + 1;
              const allLocked = readOnly || row.lock === "all"; // 行全体を変更できない
              const amountLocked = allLocked || row.lock === "amount"; // 数量・単価・税率を変更できない
              return (
                <tr key={row.key} className={row.lock !== "none" && !readOnly ? "is-locked" : undefined}>
                  <td className="col-no">{n}</td>
                  <td>
                    <input
                      aria-label={`${n}行目の品名`}
                      aria-invalid={issues?.description}
                      value={row.description}
                      disabled={allLocked}
                      list={items.length ? listId : undefined}
                      autoComplete="off"
                      onChange={(e) => changeDescription(row.key, e.target.value, !amountLocked)}
                    />
                  </td>
                  <td>
                    <input
                      className="num"
                      inputMode="decimal"
                      aria-label={`${n}行目の数量`}
                      aria-invalid={issues?.quantity}
                      value={row.quantity}
                      disabled={amountLocked}
                      onChange={(e) => update(row.key, "quantity", e.target.value)}
                      onBlur={() => tidy(row.key, "quantity")}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`${n}行目の単位`}
                      value={row.unit}
                      disabled={allLocked}
                      onChange={(e) => update(row.key, "unit", e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="num"
                      inputMode="decimal"
                      aria-label={`${n}行目の単価`}
                      aria-invalid={issues?.price}
                      value={row.unit_price}
                      placeholder="値引きは -"
                      disabled={amountLocked}
                      onChange={(e) => update(row.key, "unit_price", e.target.value)}
                      onBlur={() => tidy(row.key, "unit_price")}
                    />
                  </td>
                  {showTax && (
                  <td>
                      <select
                        aria-label={`${n}行目の税率`}
                        value={row.tax_category}
                        disabled={amountLocked}
                        onChange={(e) => update(row.key, "tax_category", e.target.value)}
                      >
                        {TAX_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </td>
                  )}
                  {withDeliveryDate && (
                    <td>
                      <input
                        type="date"
                        aria-label={`${n}行目の${dateLabel}`}
                        aria-invalid={dateRequired && showErrors && !isBlank(row) && !row.delivered_on}
                        className={row.date_confirmed ? undefined : "provisional"}
                        title={row.date_confirmed ? undefined : "仮の日付です。確認してください"}
                        value={row.delivered_on}
                        disabled={allLocked}
                        onChange={(e) => update(row.key, "delivered_on", e.target.value)}
                      />
                    </td>
                  )}
                  <td className="num amount">{amountByKey.has(row.key) ? yen(amountByKey.get(row.key)) : "―"}</td>
                  {!readOnly && (
                    <td className="col-remove">
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`${n}行目を削除`}
                        disabled={rows.length === 1 || row.lock === "all"}
                        onClick={() => onChange((list) => list.filter((r) => r.key !== row.key))}
                      >
                        ×
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!readOnly && items.length > 0 && (
        <datalist id={listId}>
          {items.map((it) => (
            <option key={it.id} value={it.name}>
              {[it.code, `${decimal(it.unit_price)}円${it.unit ? `/${it.unit}` : ""}`].filter(Boolean).join("  ")}
            </option>
          ))}
        </datalist>
      )}
      {!readOnly && (
        <button type="button" className="secondary add-row" onClick={() => onChange((list) => [...list, blankRow()])}>
          行を追加
        </button>
      )}
    </>
  );
}
