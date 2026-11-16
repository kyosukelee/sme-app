import type { PaymentTerms } from "../api";

const DAYS = [...Array.from({ length: 30 }, (_, i) => i + 1), 31];
const MONTHS = [
  { value: 0, label: "当月" },
  { value: 1, label: "翌月" },
  { value: 2, label: "翌々月" },
  { value: 3, label: "3か月後" },
];

export const dayLabel = (d: number) => (d === 31 ? "月末" : `${d}日`);

/** 支払条件の読み方(例: 翌月末、翌月10日、3か月後の月末) */
export function paymentLabel({ payment_month_offset: m, payment_day: d }: PaymentTerms): string {
  if (m === 3) return `3か月後の${d === 31 ? "月末" : `${d}日`}`;
  return `${["当", "翌", "翌々"][m]}${d === 31 ? "月末" : `月${d}日`}`;
}

/** 締め・支払条件の読み方(例: 20日締め・翌月10日払い) */
export const termsLabel = (t: PaymentTerms) => `${dayLabel(t.closing_day)}締め・${paymentLabel(t)}払い`;

/** 締め日・支払月・支払日の選択欄。自社情報と取引先で共通に使う */
export default function TermsFields({
  terms,
  onChange,
  disabled = false,
}: {
  terms: PaymentTerms;
  onChange: (t: PaymentTerms) => void;
  disabled?: boolean;
}) {
  return (
    <>
      <div className="terms-row">
        <label>
          締め日
          <select
            value={terms.closing_day}
            disabled={disabled}
            onChange={(e) => onChange({ ...terms, closing_day: Number(e.target.value) })}
          >
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {dayLabel(d)}
              </option>
            ))}
          </select>
        </label>
        <label>
          支払月
          <select
            value={terms.payment_month_offset}
            disabled={disabled}
            onChange={(e) => onChange({ ...terms, payment_month_offset: Number(e.target.value) })}
          >
            {MONTHS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          支払日
          <select
            value={terms.payment_day}
            disabled={disabled}
            onChange={(e) => onChange({ ...terms, payment_day: Number(e.target.value) })}
          >
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {dayLabel(d)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="terms-summary">{termsLabel(terms)}</p>
    </>
  );
}
