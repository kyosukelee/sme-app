// 明細の入力行と、その確認・変換。見積と受注の画面で共通に使う
import type { Calculation, LineInput, TaxCategory, TaxSummary } from "./api";

export type Row = {
  key: number;
  description: string;
  quantity: string;
  unit: string;
  unit_price: string;
  tax_category: TaxCategory;
  delivered_on: string; // 受注は納品日、請求書は取引日(yyyy-mm-dd)
  quote_line_id: string | null; // 見積から変換した受注の行だけ
  order_line_id: string | null; // 受注から作った請求書の行だけ
  line_id: string | null; // 保存済みの行のID(受注は行ごとに更新するので送り返す)
  lock: RowLock;
  date_confirmed: boolean; // 請求書の取引日が確認済みか(受注から作ったときの仮の日付は false)
};

/** 行の固定のしかた。all: すべて変更不可(請求書に載った受注の行)、amount: 数量・単価・税率だけ変更不可(受注から作った請求書の行) */
export type RowLock = "none" | "amount" | "all";

export type SavedLine = {
  description: string;
  quantity: string;
  unit: string | null;
  unit_price: string;
  tax_category: TaxCategory;
  amount: string;
  tax_rate: string;
  delivered_on?: string | null;
  quote_line_id?: string | null;
  transaction_date?: string;
  order_line_id?: string | null;
  line_id?: string;
  billed?: boolean;
  date_confirmed?: boolean;
};

export type SavedDocument = {
  lines: SavedLine[];
  tax_summaries: TaxSummary[];
  subtotal_amount: string;
  tax_amount: string;
  total_amount: string;
};

export type LiveCalc = { result: Calculation; keys: number[] };

let nextKey = 1;

export const blankRow = (): Row => ({
  key: nextKey++,
  description: "",
  quantity: "1",
  unit: "",
  unit_price: "",
  tax_category: "standard",
  delivered_on: "",
  quote_line_id: null,
  order_line_id: null,
  line_id: null,
  lock: "none",
  date_confirmed: true,
});

/** 全角の数字・記号を半角にし、桁区切りのカンマを取り除く */
export function normalizeNumber(value: string): string {
  return value
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .replace(/[．]/g, ".")
    .replace(/[－ー−]/g, "-")
    .replace(/[,，\s]/g, "");
}

const QUANTITY = /^\d{1,9}(\.\d{1,3})?$/;
const PRICE = /^-?\d{1,13}(\.\d{1,2})?$/;

export function rowIssues(row: Row) {
  const quantity = normalizeNumber(row.quantity);
  const price = normalizeNumber(row.unit_price);
  return {
    quantity: !(QUANTITY.test(quantity) && Number(quantity) > 0),
    price: !PRICE.test(price),
    description: row.description.trim() === "",
  };
}

export const isBlank = (row: Row) => !row.description.trim() && !row.unit_price.trim() && !row.unit.trim();

export const isCalculable = (row: Row) => {
  const issues = rowIssues(row);
  return !isBlank(row) && !issues.quantity && !issues.price;
};

export function toLine(row: Row): LineInput {
  return {
    description: row.description.trim(),
    quantity: normalizeNumber(row.quantity),
    unit: row.unit.trim() || null,
    unit_price: normalizeNumber(row.unit_price),
    tax_category: row.tax_category,
  };
}

export function rowsFromDocument(doc: SavedDocument, lockOf: (line: SavedLine) => RowLock = () => "none"): Row[] {
  return doc.lines.map((l) => ({
    key: nextKey++,
    description: l.description,
    quantity: l.quantity.replace(/\.?0+$/, ""),
    unit: l.unit ?? "",
    unit_price: l.unit_price.replace(/\.00$/, ""),
    tax_category: l.tax_category,
    delivered_on: l.delivered_on ?? l.transaction_date ?? "",
    quote_line_id: l.quote_line_id ?? null,
    order_line_id: l.order_line_id ?? null,
    line_id: l.line_id ?? null,
    lock: lockOf(l),
    date_confirmed: l.date_confirmed ?? true,
  }));
}

/** 保存済みの金額を、計算結果と同じ形にする(計算が返るまでの表示用) */
export function calcFromDocument(doc: SavedDocument, rows: Row[]): LiveCalc {
  return {
    result: {
      lines: doc.lines.map((l) => ({ amount: l.amount, tax_rate: l.tax_rate })),
      tax_summaries: doc.tax_summaries,
      subtotal_amount: doc.subtotal_amount,
      tax_amount: doc.tax_amount,
      total_amount: doc.total_amount,
    },
    keys: rows.map((r) => r.key),
  };
}

/** 保存前の確認。問題があれば理由を、なければ null を返す */
export function findLineProblem(rows: Row[], options: { requireDate?: string } = {}): string | null {
  if (!rows.some((r) => !isBlank(r))) return "明細を1行以上入力してください。";
  const bad = rows.findIndex((r) => !isBlank(r) && Object.values(rowIssues(r)).some(Boolean));
  if (bad >= 0) return `${bad + 1}行目の品名・数量・単価を確認してください。`;
  if (options.requireDate) {
    const noDate = rows.findIndex((r) => !isBlank(r) && !r.delivered_on);
    if (noDate >= 0) return `${noDate + 1}行目の${options.requireDate}を入力してください。`;
  }
  return null;
}
