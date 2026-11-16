import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type Customer, type OpenInvoice, type Receipt } from "../api";
import { jdate, todayIso, yen } from "../format";
import { CustomerSelect } from "./QuoteEditor";

/**
 * 取引先ごとのまとめ入金。取引先が複数の請求書の分をまとめて振り込んだとき、
 * 1回の入金として登録し、未入金の請求書へ古い順に割り当てる
 */
export default function Receipts({ tenantId, initialCustomerId }: { tenantId: string; initialCustomerId: string | null }) {
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [customerId, setCustomerId] = useState(initialCustomerId ?? "");
  const [open, setOpen] = useState<{ invoices: OpenInvoice[]; total_balance: string } | null>(null);
  const [history, setHistory] = useState<Receipt[]>([]);
  const [receivedOn, setReceivedOn] = useState(todayIso());
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [preview, setPreview] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);

  useEffect(() => {
    api.customers(tenantId, true).then(setCustomers).catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  const reload = useCallback(async () => {
    if (!customerId) {
      setOpen(null);
      setHistory([]);
      return;
    }
    const [o, h] = await Promise.all([api.openInvoices(tenantId, customerId), api.receipts(tenantId, customerId)]);
    setOpen(o);
    setHistory(h);
  }, [tenantId, customerId]);

  useEffect(() => {
    setPreview(null);
    reload().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [reload]);

  const normalized = normalizeDigits(amount);

  function body(dryRun: boolean) {
    return { customer_id: customerId, received_on: receivedOn, amount: normalized, memo: memo.trim() || null, dry_run: dryRun };
  }

  async function check(e: FormEvent) {
    e.preventDefault();
    if (!/^\d+$/.test(normalized) || Number(normalized) <= 0) {
      setMessage({ kind: "error", text: "入金額を円単位の数字で入力してください。" });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      setPreview(await api.createReceipt(tenantId, body(true)));
    } catch (err) {
      setPreview(null);
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function register() {
    setBusy(true);
    try {
      const saved = await api.createReceipt(tenantId, body(false));
      setPreview(null);
      setAmount("");
      setMemo("");
      setMessage({ kind: "done", text: `${yen(saved.amount)}の入金を${saved.allocations.length}件の請求書に割り当てました` });
      await reload();
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function cancel(r: Receipt) {
    if (!r.id || !window.confirm(`${jdate(r.received_on)}の入金 ${yen(r.amount)} を取り消しますか？割り当てた請求書は未入金に戻ります。`)) return;
    setBusy(true);
    try {
      await api.deleteReceipt(tenantId, r.id);
      setMessage({ kind: "done", text: "入金を取り消しました" });
      await reload();
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>入金</h1>
      </div>
      <p className="hint page-note">
        取引先が複数の請求書の分をまとめて振り込んだときに使います。入金額は、未入金の請求書へ古い順に割り当てます。
        1枚の請求書だけの入金は、請求書の画面からも登録できます。
      </p>

      <div className="stack">
        <section className="panel">
          <div className="field-row">
            <CustomerSelect
              customers={customers}
              value={customerId}
              invalid={false}
              includeInactive /* 使用中止にした取引先にも、未請求の残りや未入金があれば処理できるようにする */
              onChange={(id) => {
                setCustomerId(id);
                setMessage(null);
                window.history.replaceState(null, "", `#/receipts${id ? `?customer=${id}` : ""}`);
              }}
            />
          </div>
        </section>

        {message && (
          <p className={message.kind === "done" ? "notice" : "alert"} role="status">
            {message.text}
          </p>
        )}

        {customerId && open && (
          <div className="bottom-row">
            <section className="panel lines-panel">
              <h2>未入金の請求書</h2>
              {open.invoices.length === 0 ? (
                <p className="placeholder">この取引先に未入金の請求書はありません。</p>
              ) : (
                <div className="table-wrap flat">
                  <table className="nowrap">
                    <thead>
                      <tr>
                        <th scope="col">請求番号</th>
                        <th scope="col">支払期限</th>
                        <th scope="col" className="num">
                          残額
                        </th>
                        {preview && (
                          <th scope="col" className="num">
                            今回の割り当て
                          </th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {open.invoices.map((i) => {
                        const share = preview?.allocations.find((a) => a.invoice_id === i.invoice_id);
                        return (
                          <tr key={i.invoice_id}>
                            <td>
                              <a href={`#/invoices/${i.invoice_id}`}>{i.invoice_number}</a>
                              {i.version > 1 && <span className="honorific">第{i.version}版</span>}
                            </td>
                            <td>
                              {jdate(i.due_date)}
                              {i.is_overdue && <span className="badge is-expired inline">期限超過</span>}
                            </td>
                            <td className="num">{yen(i.balance)}</td>
                            {preview && (
                              <td className="num">
                                {share ? (
                                  <>
                                    <strong>{yen(share.amount)}</strong>{" "}
                                    <small className="after">{Number(share.balance_after) === 0 ? "入金済に" : `残り ${yen(share.balance_after)}`}</small>
                                  </>
                                ) : (
                                  "―"
                                )}
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="closing-footer">
                未入金の合計 <strong>{yen(open.total_balance)}</strong>
              </p>
            </section>

            <form className="panel" onSubmit={check}>
              <h2>入金を登録</h2>
              <label>
                入金日
                <input
                  type="date"
                  required
                  value={receivedOn}
                  onChange={(e) => {
                    setReceivedOn(e.target.value);
                    setPreview(null);
                  }}
                />
              </label>
              <label>
                入金額(円)
                <input
                  className="num"
                  inputMode="numeric"
                  value={amount}
                  placeholder={open.total_balance}
                  onChange={(e) => {
                    setAmount(e.target.value);
                    setPreview(null);
                  }}
                  onBlur={() => setAmount((v) => (v ? Number(normalizeDigits(v)).toLocaleString("ja-JP") : v))}
                />
              </label>
              <label>
                メモ
                <input value={memo} placeholder="例: 11月分 振込" onChange={(e) => setMemo(e.target.value)} />
              </label>
              <div className="actions">
                {preview ? (
                  <>
                    <button type="button" onClick={register} disabled={busy}>
                      {busy ? "登録しています…" : `この割り当てで登録(${preview.allocations.length}件)`}
                    </button>
                    <button type="button" className="secondary" onClick={() => setPreview(null)} disabled={busy}>
                      金額を直す
                    </button>
                  </>
                ) : (
                  <>
                    <button type="submit" disabled={busy || open.invoices.length === 0}>
                      割り当てを確認
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setAmount(open.total_balance)}
                      disabled={busy || open.invoices.length === 0}
                    >
                      未入金の合計を入れる
                    </button>
                  </>
                )}
              </div>
              {preview && <p className="hint">左の表の「今回の割り当て」を確認してから登録してください。</p>}
            </form>
          </div>
        )}

        {customerId && history.length > 0 && (
          <section className="panel">
            <h2>まとめ入金の履歴</h2>
            <ul className="history">
              {history.map((r) => (
                <li key={r.id}>
                  <span>
                    {jdate(r.received_on)} <strong>{yen(r.amount)}</strong>
                    {r.memo && <small>{r.memo}</small>}
                    <small>
                      →{" "}
                      {r.allocations.map((a, i) => (
                        <span key={a.invoice_id}>
                          {i > 0 && "、"}
                          <a href={`#/invoices/${a.invoice_id}`}>{a.invoice_number}</a> {yen(a.amount)}
                        </span>
                      ))}
                    </small>
                  </span>
                  <button type="button" className="link-button" onClick={() => cancel(r)} disabled={busy}>
                    取り消す
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}

/** 全角数字を半角にし、桁区切りと空白を取り除く */
function normalizeDigits(value: string): string {
  return value.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)).replace(/[,，\s]/g, "");
}
