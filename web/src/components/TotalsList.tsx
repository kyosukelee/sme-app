import type { TaxSummary } from "../api";
import { TAX_SUMMARY_LABELS, yen } from "../format";

type Props = {
  summaries: TaxSummary[];
  subtotal: string | number;
  tax: string | number;
  total: string | number;
  exempt?: boolean; // 免税事業者: 消費税を表示せず、合計だけを出す
  withholding?: { amount: string | number; net: string | number } | null; // 源泉徴収する請求書
};

/** 税率別の小計・消費税と合計(源泉徴収があれば差引ご請求額まで) */
export default function TotalsList({ summaries, subtotal, tax, total, exempt = false, withholding = null }: Props) {
  const totalLabel = exempt ? "合計" : "合計(税込)";
  return (
    <dl>
      {!exempt && (
        <>
          {summaries.map((s) => (
            <div key={`${s.tax_category}-${s.tax_rate}`} className="sub">
              <dt>{TAX_SUMMARY_LABELS[s.tax_category]}</dt>
              <dd>
                {yen(s.taxable_amount)}
                {Number(s.tax_rate) > 0 && <small>消費税 {yen(s.tax_amount)}</small>}
              </dd>
            </div>
          ))}
          <div>
            <dt>小計</dt>
            <dd>{yen(subtotal)}</dd>
          </div>
          <div>
            <dt>消費税</dt>
            <dd>{yen(tax)}</dd>
          </div>
        </>
      )}
      {withholding ? (
        <>
          <div>
            <dt>{totalLabel}</dt>
            <dd>{yen(total)}</dd>
          </div>
          <div className="minus">
            <dt>源泉徴収税額</dt>
            <dd>−{yen(withholding.amount)}</dd>
          </div>
          <div className="grand">
            <dt>差引ご請求額</dt>
            <dd>{yen(withholding.net)}</dd>
          </div>
        </>
      ) : (
        <div className="grand">
          <dt>{totalLabel}</dt>
          <dd>{yen(total)}</dd>
        </div>
      )}
    </dl>
  );
}
