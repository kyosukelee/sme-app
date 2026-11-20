// 申請・承認の画面で共通に使う部品(状態の表示、承認ルートの表示、入力値の表示、ファイルの読み込み)
import type { FormField, RequestStatus, RouteStep, UploadFile } from "../../api";
import { jdate, jdatetime } from "../../format";

export const REQUEST_STATUS_LABELS: Record<RequestStatus, string> = {
  draft: "下書き",
  pending: "承認待ち",
  returned: "差戻し",
  approved: "承認済",
  rejected: "却下",
  withdrawn: "取下げ",
};

export function RequestStatusBadge({ status }: { status: RequestStatus }) {
  return <span className={`badge req-${status}`}>{REQUEST_STATUS_LABELS[status]}</span>;
}

/** 日時を短く(例: 11月20日 14:05)。年が今年でなければ年も付ける */
export function shortDateTime(value: string | null): string {
  if (!value) return "―";
  const d = new Date(value);
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  return d.getFullYear() === new Date().getFullYear() ? `${md} ${time}` : jdatetime(value);
}

/** 入力項目の値を表示用にする */
export function fieldValue(field: FormField, value: string | undefined): string {
  if (value === undefined || value === "") return "―";
  if (field.type === "date") return jdate(value);
  return value;
}

const STEP_STATE: Record<RouteStep["status"], { mark: string; text: string }> = {
  approved: { mark: "✓", text: "承認" },
  pending: { mark: "", text: "承認待ち" },
  waiting: { mark: "", text: "これから" },
  skipped: { mark: "–", text: "省略" },
  returned: { mark: "↩", text: "差戻し" },
  rejected: { mark: "×", text: "却下" },
  cancelled: { mark: "–", text: "取消" },
};

function approverNames(step: RouteStep): string {
  const names = step.approvers.map((a) => a.display_name);
  if (names.length === 0) return "";
  if (names.length === 1) return names[0];
  return `${names.join("・")}(どなたか1人)`;
}

/**
 * 承認ルートの進み具合。PC では左から右へ、スマホでは上から下へ並べる。
 * start(申請者)と end(承認済)の印を両端に置き、どこまで進んだかを一目で分かるようにする
 */
export function RouteView({
  steps,
  applicant,
  submittedAt,
  finished,
  preview = false,
}: {
  steps: RouteStep[];
  applicant: string;
  submittedAt?: string | null;
  finished?: "approved" | "rejected" | "withdrawn" | null;
  preview?: boolean;
}) {
  const endState = finished === "approved" ? "is-approved" : finished ? "is-closed" : "is-waiting";
  return (
    <ol className="route" aria-label="承認ルート">
      <li className={`route-step is-start${submittedAt ? " is-reached" : ""}`}>
        <span className="route-mark" aria-hidden="true">
          申
        </span>
        <span className="route-text">
          <span className="route-label">申請</span>
          <span className="route-names">{applicant}</span>
          <span className="route-state">{submittedAt ? shortDateTime(submittedAt) : preview ? "これから申請" : "未申請"}</span>
        </span>
      </li>
      {steps.map((s) => {
        const state = STEP_STATE[s.status];
        const reached = ["approved", "pending", "returned", "rejected"].includes(s.status);
        return (
          <li key={s.step_no} className={`route-step is-${s.status}${reached ? " is-reached" : ""}`}>
            <span className="route-mark" aria-hidden="true">
              {state.mark || s.step_no}
            </span>
            <span className="route-text">
              <span className="route-label">
                {s.step_no}. {s.label}
              </span>
              <span className="route-names">
                {s.acted_by && s.status !== "skipped" ? s.acted_by.display_name : approverNames(s) || "―"}
              </span>
              <span className="route-state">
                {s.status === "skipped"
                  ? (s.skip_reason ?? "省略")
                  : s.acted_at
                    ? `${state.text} ${shortDateTime(s.acted_at)}`
                    : preview && s.status === "waiting"
                      ? "承認者"
                      : state.text}
              </span>
            </span>
          </li>
        );
      })}
      <li className={`route-step is-end ${endState}${finished === "approved" ? " is-reached" : ""}`}>
        <span className="route-mark" aria-hidden="true">
          {finished === "approved" ? "✓" : "完"}
        </span>
        <span className="route-text">
          <span className="route-label">完了</span>
          <span className="route-state">
            {finished === "approved"
              ? "承認済"
              : finished === "rejected"
                ? "却下"
                : finished === "withdrawn"
                  ? "取下げ"
                  : "すべての承認で完了"}
          </span>
        </span>
      </li>
    </ol>
  );
}

/** 添付ファイルを API に送る形にする */
export async function readUpload(file: File): Promise<UploadFile> {
  if (file.size > 10 * 1024 * 1024) throw new Error(`「${file.name}」が大きすぎます(10MBまで)`);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return { file_name: file.name, content_type: file.type || null, content_base64: btoa(binary) };
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}
