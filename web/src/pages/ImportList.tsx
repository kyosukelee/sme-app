import { useEffect, useRef, useState, type DragEvent } from "react";
import { api, type ImportFilter, type ImportListItem, type ImportSettings, type ImportStatus } from "../api";
import { jdatetime, yen } from "../format";
import { readUpload } from "./requests/common";

const TABS: { value: ImportFilter; label: string }[] = [
  { value: "open", label: "要対応" },
  { value: "confirmed", label: "確定済み" },
  { value: "discarded", label: "破棄" },
  { value: "all", label: "すべて" },
];

export const IMPORT_STATUS: Record<ImportStatus, { label: string; className: string }> = {
  queued: { label: "処理待ち", className: "is-draft" },
  processing: { label: "読み取り中", className: "is-draft" },
  review: { label: "要確認", className: "is-issued" },
  failed: { label: "失敗", className: "is-expired" },
  confirmed: { label: "確定済み", className: "is-ordered" },
  discarded: { label: "破棄", className: "is-lost" },
};

export function ImportStatusBadge({ status }: { status: ImportStatus }) {
  const s = IMPORT_STATUS[status];
  return <span className={`badge ${s.className}`}>{s.label}</span>;
}

const isRunning = (i: { status: ImportStatus }) => i.status === "queued" || i.status === "processing";

/** 注文書の取り込みの一覧。PDF を選ぶ(ドロップする)と取り込みを始める */
export default function ImportList({ tenantId }: { tenantId: string }) {
  const [settings, setSettings] = useState<ImportSettings | null>(null);
  const [filter, setFilter] = useState<ImportFilter>("open");
  const [items, setItems] = useState<ImportListItem[] | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.importSettings(tenantId).then(setSettings).catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [tenantId]);

  useEffect(() => {
    let timer: number | undefined;
    let alive = true;
    api
      .imports(tenantId, filter)
      .then((list) => {
        if (!alive) return;
        setItems(list);
        // 読み取り中の取り込みがあれば、終わるまで2秒ごとに読み直す
        if (list.some(isRunning)) timer = window.setTimeout(() => setReload((n) => n + 1), 2000);
      })
      .catch((e: Error) => alive && setMessage({ kind: "error", text: e.message }));
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [tenantId, filter, reload]);

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    setBusy(true);
    setMessage(null);
    const failures: string[] = [];
    let done = 0;
    for (const file of list) {
      try {
        const upload = await readUpload(file);
        await api.uploadImport(tenantId, { file_name: upload.file_name, content_base64: upload.content_base64 });
        done += 1;
      } catch (e) {
        failures.push(`${file.name}: ${(e as Error).message}`);
      }
    }
    setBusy(false);
    if (input.current) input.current.value = "";
    setFilter("open");
    setReload((n) => n + 1);
    if (failures.length) setMessage({ kind: "error", text: failures.join("\n") });
    else setMessage({ kind: "done", text: `${done}件の注文書を受け付けました。読み取りが終わると「要確認」になります。` });
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (!busy && settings?.enabled) uploadFiles(e.dataTransfer.files);
  }

  const ready = settings?.available && settings.enabled;

  return (
    <>
      <div className="page-head">
        <a className="back" href="#/orders">
          受注の一覧
        </a>
        <h1>注文書の取り込み</h1>
        {items && <p className="count">{items.length}件</p>}
      </div>

      <p className="lead">
        お客様から届いた注文書のPDFをAIが読み取り、受注の下書きを作ります。取引先・金額・税率を確認してから確定すると、受注として登録されます。
      </p>

      {settings && !ready && (
        <p className="notice-warn page-note">
          {!settings.available
            ? "このサーバーでは AI の提供元が設定されていないため、注文書の取り込みは使えません(AI_PROVIDER)。"
            : settings.can_edit
              ? "注文書の取り込みはオフになっています。"
              : "注文書の取り込みはオフになっています。使うときは管理者に依頼してください。"}
          {settings.available && settings.can_edit && (
            <>
              {" "}
              <a href="#/settings/company">自社情報</a>の「AIによる注文書の取り込み」でオンにしてください。
            </>
          )}
        </p>
      )}

      {ready && (
        <section
          className={`panel drop-zone${dragging ? " is-dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <p>
            注文書のPDFをここにドロップするか、
            <button type="button" className="link-button" onClick={() => input.current?.click()} disabled={busy}>
              ファイルを選んでください
            </button>
            (1ファイル10MB・10ページまで。複数選べます)
          </p>
          <input
            ref={input}
            type="file"
            accept="application/pdf,.pdf"
            multiple
            hidden
            onChange={(e) => e.target.files && uploadFiles(e.target.files)}
          />
          <p className="hint">
            読み取りに使うAI: {settings.provider}。PDFの内容をこのAIに送ります。
            {settings.provider.includes("fake") && " 見本用(fake)は、docs/samples/orders の見本の注文書だけを読み取れます。"}
          </p>
          {busy && <p className="hint">送っています…</p>}
        </section>
      )}

      {message && (
        <p className={message.kind === "done" ? "notice page-note" : "alert"} role="status" style={{ whiteSpace: "pre-line" }}>
          {message.text}
        </p>
      )}

      <div className="tabs" role="tablist" aria-label="取り込みの状態">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={filter === tab.value}
            className="tab"
            onClick={() => setFilter(tab.value)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {items === null ? (
        <p className="placeholder">読み込み中…</p>
      ) : items.length === 0 ? (
        <p className="placeholder">{filter === "open" ? "確認待ちの注文書はありません。" : "この状態の取り込みはありません。"}</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">取り込み日時</th>
                <th scope="col">ファイル</th>
                <th scope="col">注文主(読み取り)</th>
                <th scope="col" className="num">
                  合計(注文書)
                </th>
                <th scope="col">状態</th>
                <th scope="col">確認すること</th>
                <th scope="col">受注</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id}>
                  <td>{jdatetime(i.created_at)}</td>
                  <td className="file-cell">
                    <a href={`#/imports/${i.id}`}>{i.file_name}</a>
                  </td>
                  <td>{i.issuer_name ?? "―"}</td>
                  <td className="num">{yen(i.doc_total)}</td>
                  <td>
                    <ImportStatusBadge status={i.status} />
                  </td>
                  <td className="check-summary">
                    {i.status === "failed" ? (
                      <span className="error-text">{i.error_message}</span>
                    ) : i.status === "review" ? (
                      <>
                        {i.needs_customer && <span className="todo-text">取引先の選択待ち</span>}
                        {i.error_count > 0 && <span className="error-text">要修正 {i.error_count}件</span>}
                        {i.warning_count > 0 && <span className="warn-text">要確認 {i.warning_count}件</span>}
                        {!i.needs_customer && i.error_count + i.warning_count === 0 && "確定できます"}
                      </>
                    ) : (
                      "―"
                    )}
                  </td>
                  <td>{i.order_id ? <a href={`#/orders/${i.order_id}`}>{i.order_number}</a> : "―"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
