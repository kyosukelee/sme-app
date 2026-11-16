// 表示用の整形

const yenFormat = new Intl.NumberFormat("ja-JP");

/** 円の表示(例: 177,613円)。API は金額を文字列で返す */
export function yen(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "―";
  return `${yenFormat.format(Number(value))}円`;
}

/** 数量・単価の表示。小数部の不要な0を落とす(1.500 → 1.5) */
export function decimal(value: string): string {
  const [int, frac = ""] = value.split(".");
  const trimmed = frac.replace(/0+$/, "");
  return yenFormat.format(Number(int)) + (trimmed ? `.${trimmed}` : "");
}

/** 日付の表示(例: 2026年11月12日) */
export function jdate(value: string | null): string {
  if (!value) return "―";
  const [y, m, d] = value.split("-").map(Number);
  return `${y}年${m}月${d}日`;
}

export const TAX_OPTIONS = [
  { value: "standard", label: "10%" },
  { value: "reduced", label: "8%(軽減)" },
  { value: "exempt", label: "非課税" },
  { value: "non_taxable", label: "不課税" },
  { value: "export", label: "免税" },
] as const;

export const TAX_SUMMARY_LABELS: Record<string, string> = {
  standard: "10%対象",
  reduced: "8%対象(軽減税率)",
  exempt: "非課税",
  non_taxable: "不課税",
  export: "免税",
};

/** 今日の日付(yyyy-mm-dd、パソコンの時計の日付) */
export function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 日時の表示(例: 2026年11月12日 14:05) */
export function jdatetime(value: string): string {
  const d = new Date(value);
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
