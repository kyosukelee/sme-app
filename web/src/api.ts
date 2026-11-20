// API との通信。会社を選んだ後の通信には X-Tenant-Id ヘッダーを付ける
// 金額・数量は誤差が出ないよう、API とは文字列でやりとりする

export type Tenant = { id: string; name: string; role: "admin" | "member" };

export type Session = { user_id: string; auth_mode: "dev" | "supabase" };

export type Member = {
  user_id: string;
  display_name: string;
  email: string | null;
  department: string | null;
  manager_user_id: string | null;
  manager_name: string | null;
  role: "admin" | "member";
  is_active: boolean;
  groups: { id: string; name: string }[];
  is_me: boolean;
};

export type MemberInput = Pick<Member, "display_name" | "email" | "department" | "manager_user_id" | "role" | "is_active">;

export type ApproverGroup = {
  id: string;
  name: string;
  members: { user_id: string; display_name: string; is_active: boolean }[];
};

export type Customer = {
  id: string;
  name: string;
  honorific: string;
  contact_name: string | null;
  address: string | null;
  email: string | null;
  withholding_default: boolean;
  terms: PaymentTerms | null; // 取引先ごとの締め日・支払日。null なら会社の設定に従う
  is_active: boolean;
  payer_names: string[]; // 振込名義(銀行の明細に出るカナの名前)。入金消込で使う
};

export type CustomerInput = Omit<Customer, "id">;

export type Item = {
  id: string;
  code: string | null;
  name: string;
  unit: string | null;
  unit_price: string;
  tax_category: TaxCategory;
  is_active: boolean;
};

export type ItemInput = Omit<Item, "id">;

export type TaxCategory = "standard" | "reduced" | "exempt" | "non_taxable" | "export";
export type QuoteStatus = "draft" | "issued" | "ordered" | "lost";
export type QuoteFilter = "all" | "draft" | "issued" | "ordered" | "closed";

export type TaxSummary = {
  tax_category: TaxCategory;
  tax_rate: string;
  taxable_amount: string;
  tax_amount: string;
};

export type LineInput = {
  description: string;
  quantity: string;
  unit: string | null;
  unit_price: string;
  tax_category: TaxCategory;
};

export type QuoteLine = LineInput & { line_id: string; line_no: number; amount: string; tax_rate: string };

export type QuoteListItem = {
  id: string;
  quote_number: string | null;
  status: QuoteStatus;
  is_expired: boolean;
  customer_name: string;
  title: string | null;
  issue_date: string | null;
  valid_until: string | null;
  total_amount: string;
};

export type Quote = QuoteListItem & {
  customer_id: string;
  customer_honorific: string;
  customer_address: string | null;
  remarks: string | null;
  subtotal_amount: string;
  tax_amount: string;
  updated_at: string;
  order_id: string | null;
  lines: QuoteLine[];
  tax_summaries: TaxSummary[];
};

export type OrderStatus = "open" | "billed";
export type OrderFilter = "all" | OrderStatus;

export type OrderLine = QuoteLine & { quote_line_id: string | null; delivered_on: string | null; billed: boolean };

export type OrderListItem = {
  id: string;
  order_number: string;
  status: OrderStatus;
  customer_name: string;
  title: string | null;
  order_date: string;
  delivery_due_date: string | null;
  total_amount: string;
  quote_number: string | null;
  customer_order_ref: string | null; // お客様の注文番号
};

export type Order = OrderListItem & {
  customer_id: string;
  customer_honorific: string;
  customer_address: string | null;
  remarks: string | null;
  subtotal_amount: string;
  tax_amount: string;
  updated_at: string;
  quote_id: string | null;
  source_import_id: string | null; // 注文書の取り込みから作った受注なら、その取り込み
  lines: OrderLine[];
  tax_summaries: TaxSummary[];
  acknowledgments: { version: number; issued_at: string }[];
  invoices: { id: string; invoice_number: string | null; version: number; status: InvoiceStatus }[];
  unbilled_line_count: number;
};

export type OrderInput = {
  customer_id: string;
  title: string | null;
  remarks: string | null;
  order_date: string | null;
  delivery_due_date: string | null;
  customer_order_ref: string | null;
  lines: (LineInput & { line_id: string | null; quote_line_id: string | null; delivered_on: string | null })[];
};

// ---- 注文書の取り込み(AI) ----
export type ImportStatus = "queued" | "processing" | "review" | "failed" | "confirmed" | "discarded";
export type ImportFilter = "all" | "open" | "confirmed" | "discarded";

export type ImportSettings = { available: boolean; enabled: boolean; provider: string; can_edit: boolean };

export type ImportCheck = { id: string; level: "error" | "warning"; message: string; field: string | null };

export type DraftLine = {
  description: string;
  quantity: string;
  unit: string;
  unit_price: string;
  tax_category: TaxCategory;
  item_id: string | null;
  item_code?: string | null;
  doc_amount?: string | null; // 注文書に書かれていた金額(照合用)
  doc_tax_rate?: "10" | "8" | "0" | null;
  page?: number | null;
};

export type ImportDraft = {
  customer_id: string | null;
  customer_order_ref: string;
  order_date: string; // 未入力は空文字
  delivery_due_date: string;
  title: string;
  remarks: string;
  lines: DraftLine[];
};

export type ExtractedOrder = {
  issuer_name: string | null;
  issuer_address: string | null;
  issuer_phone: string | null;
  recipient_name: string | null;
  customer_order_ref: string | null;
  order_date: string | null;
  delivery_due_date: string | null;
  subtotal: string | null;
  tax_total: string | null;
  total: string | null;
  prices_include_tax: boolean | null;
};

export type CustomerCandidate = { customer_id: string; name: string; score: number; reasons: string[] };

export type ImportListItem = {
  id: string;
  status: ImportStatus;
  file_name: string;
  created_at: string;
  updated_at: string;
  issuer_name: string | null;
  doc_total: string | null;
  error_count: number; // 直すまで確定できない項目(取引先の選択待ちは含めない)
  warning_count: number;
  needs_customer: boolean; // 取引先をまだ選んでいない
  error_message: string | null;
  order_id: string | null;
  order_number: string | null;
};

export type ImportDetail = ImportListItem & {
  page_count: number | null;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  classification: { doc_type: string; reason: string } | null;
  extracted: ExtractedOrder | null;
  draft: ImportDraft | null;
  checks: ImportCheck[];
  customer_candidates: CustomerCandidate[];
  discard_reason: string | null;
  confirmed_at: string | null;
};

export type InvoiceStatus = "draft" | "issued" | "paid" | "void";
export type InvoiceFilter = "all" | "draft" | "unpaid" | "paid" | "void";

export type InvoiceLine = QuoteLine & { order_line_id: string | null; transaction_date: string; date_confirmed: boolean };

export type InvoiceListItem = {
  id: string;
  invoice_number: string | null;
  version: number;
  status: InvoiceStatus;
  customer_name: string;
  title: string | null;
  issue_date: string | null;
  due_date: string | null;
  total_amount: string;
  withholding: boolean;
  withholding_amount: string;
  net_amount: string; // 差引ご請求額(合計 − 源泉徴収税額)。入金はこの額を基準にする
  paid_amount: string;
  balance: string;
  is_overdue: boolean;
};

export type Invoice = InvoiceListItem & {
  customer_id: string;
  customer_honorific: string;
  customer_address: string | null;
  remarks: string | null;
  subtotal_amount: string;
  tax_amount: string;
  issuer_name: string | null;
  issuer_registration_number: string | null;
  issuer_representative: string | null;
  issuer_tax_exempt: boolean;
  previous_version_id: string | null;
  superseded_at: string | null;
  voided_at: string | null;
  updated_at: string;
  correction_draft_id: string | null;
  default_due_date: string | null;
  closing_date: string | null;
  carried_over_amount: string;
  carryovers: { source_invoice_id: string; source_number: string; source_version: number; source_issue_date: string; balance: string }[];
  amount_due: string;
  lines: InvoiceLine[];
  tax_summaries: TaxSummary[];
  payments: { id: string; paid_on: string; amount: string; memo: string | null; created_at: string; receipt_id: string | null }[];
  versions: { id: string; version: number; status: InvoiceStatus; issue_date: string | null; superseded_at: string | null }[];
  orders: { id: string; order_number: string }[];
};

export type InvoiceInput = {
  customer_id: string;
  title: string | null;
  remarks: string | null;
  due_date: string | null;
  withholding: boolean | null;
  lines: (LineInput & { order_line_id: string | null; transaction_date: string; date_confirmed: boolean })[];
};

export type BillableLine = {
  order_id: string;
  order_number: string;
  order_title: string | null;
  order_line_id: string;
  line_no: number;
  description: string;
  quantity: string;
  unit: string | null;
  unit_price: string;
  amount: string;
  tax_category: TaxCategory;
  delivered_on: string;
};

export type Billable = {
  closing_date: string;
  period_start: string;
  due_date: string;
  lines: BillableLine[];
  missing_delivery_date: number;
};

export type OpenInvoice = {
  invoice_id: string;
  invoice_number: string;
  version: number;
  title: string | null;
  issue_date: string;
  due_date: string | null;
  total_amount: string;
  balance: string;
  is_overdue: boolean;
};

export type Receipt = {
  id: string | null;
  customer_id: string;
  customer_name: string;
  received_on: string;
  amount: string;
  fee_amount: string; // 振込手数料として消し込んだ額
  memo: string | null;
  created_at: string | null;
  allocations: {
    invoice_id: string;
    invoice_number: string;
    version: number;
    amount: string;
    fee_amount: string;
    balance_after: string;
  }[];
};

// ---- 入金消込 ----
export type Encoding = "auto" | "utf-8" | "shift_jis";

export type BankFormatInput = {
  name: string;
  encoding: Encoding;
  skip_rows: number;
  date_col: number;
  amount_col: number;
  payer_col: number;
  memo_col: number | null;
};

export type BankFormat = BankFormatInput & { id: string };

export type BankPreview = {
  encoding: "utf-8" | "shift_jis";
  rows: string[][];
  total_rows: number;
  guess: { skip_rows: number; date_col: number | null; amount_col: number | null; payer_col: number | null; memo_col: number | null };
};

export type BankImportResult = {
  id: string;
  imported: number;
  duplicates: number;
  skipped: number;
  problems: { row: number; message: string }[];
};

export type BankImportSummary = {
  id: string;
  filename: string | null;
  format_name: string | null;
  imported_count: number;
  duplicate_count: number;
  matched_count: number;
  created_at: string;
};

export type CandidateInvoice = {
  invoice_id: string;
  invoice_number: string;
  version: number;
  title: string | null;
  issue_date: string;
  due_date: string | null;
  balance: string;
};

export type MatchCandidate = {
  customer_id: string;
  customer_name: string;
  basis: "name_amount" | "amount" | "name"; // 名義と金額 / 金額だけ / 名義だけ
  match: "exact" | "fee" | "partial"; // ちょうど / 振込手数料の差額あり / 一部入金
  fee_amount: string;
  invoices: CandidateInvoice[];
};

export type StatementStatus = "open" | "matched" | "ignored";

export type BankStatement = {
  id: string;
  txn_date: string;
  amount: string;
  payer_name: string;
  memo: string | null;
  status: StatementStatus;
  ignore_reason: string | null;
  receipt: { id: string; customer_id: string; customer_name: string; fee_amount: string; invoice_numbers: string[] } | null;
  candidates: MatchCandidate[];
};

// ---- 仕訳CSV ----
export type Software = "kanjo_bugyo" | "yayoi" | "freee" | "mf";
export type AccountKey = "receivable" | "sales" | "bank" | "fee" | "withholding";
export type TaxKey =
  | "sales_standard"
  | "sales_reduced"
  | "sales_exempt"
  | "sales_non_taxable"
  | "sales_export"
  | "out_of_scope"
  | "purchase_standard";

export type JournalSettingsInput = {
  software: Software;
  sales_date_basis: "transaction" | "issue";
  customer_as_sub: boolean;
  accounts: Record<AccountKey, { name: string; code: string; sub: string }>;
  tax_labels: Record<Software, Record<TaxKey, string>>;
};

export type JournalSettings = JournalSettingsInput & {
  can_edit: boolean;
  defaults: Record<Software, Record<TaxKey, string>>;
};

export type JournalSide = { account: string; sub: string; tax: string; amount: string; tax_amount: string };

export type JournalEntry = {
  kind: "entry" | "reversal";
  source_type: "invoice" | "receipt" | "payment";
  date: string;
  summary: string;
  lines: { debit: JournalSide; credit: JournalSide }[];
};

export type JournalPreview = {
  software: Software;
  software_name: string;
  entries: JournalEntry[];
  before_count: number;
  problem: string | null;
};

export type JournalExport = {
  id: string;
  software: Software;
  software_name: string;
  date_from: string;
  date_to: string;
  entry_count: number;
  created_at: string;
  is_latest: boolean;
};

export type StatementsResponse = {
  statements: BankStatement[];
  counts: Record<StatementStatus, number>;
  fee_limit: string;
};

export type DocType = "quote" | "order" | "invoice";

export type IssuedDocument = {
  id: string;
  doc_type: DocType;
  document_id: string;
  document_number: string;
  version: number;
  transaction_date: string;
  total_amount: string;
  customer_name: string;
  issued_at: string;
  superseded: boolean;
};

export type DocumentQuery = {
  doc_type?: DocType;
  date_from?: string;
  date_to?: string;
  amount_min?: string;
  amount_max?: string;
  customer?: string;
  number?: string;
};

export type CompanyProfile = {
  business_type: "corporation" | "sole_proprietor";
  name: string;
  representative_name: string | null;
  tax_exempt: boolean;
  postal_code: string | null;
  address: string | null;
  phone: string | null;
  registration_number: string | null;
  bank_account: string | null;
};

export type PaymentTerms = { closing_day: number; payment_month_offset: number; payment_day: number };

export type Company = { profile: CompanyProfile | null; terms: PaymentTerms; bank_fee_limit: number; can_edit: boolean };

export type QuoteInput = {
  customer_id: string;
  title: string | null;
  remarks: string | null;
  lines: LineInput[];
};

export type Calculation = {
  lines: { amount: string; tax_rate: string }[];
  tax_summaries: TaxSummary[];
  subtotal_amount: string;
  tax_amount: string;
  total_amount: string;
  withholding_amount?: string;
  net_amount?: string | null;
};

// ---------------------------------------------------------------------
// ワークフロー(申請・承認)
// ---------------------------------------------------------------------
export type RequestStatus = "draft" | "pending" | "returned" | "approved" | "rejected" | "withdrawn";
export type RequestBox = "todo" | "mine" | "all";
export type ApproverKind = "manager" | "group" | "user";

/** 申請種別の入力項目の定義 */
export type FormField = {
  key: string;
  label: string;
  type: "text" | "textarea" | "date" | "number" | "select";
  required?: boolean;
  options?: string[];
  max_length?: number;
  min?: number;
  after?: string;
};

export type RouteStepDef = {
  step_no: number;
  approver_kind: ApproverKind;
  approver_group_id: string | null;
  approver_user_id: string | null;
  min_amount: string | null;
  label: string;
};

export type RequestType = {
  id: string;
  code: string;
  name: string;
  description: string | null;
  form_schema: FormField[];
  amount_mode: "required" | "optional" | "none";
  amount_label: string;
  is_active: boolean;
  steps: RouteStepDef[];
};

export type RequestTypeInput = {
  name: string;
  description: string | null;
  is_active: boolean;
  steps: { approver_kind: ApproverKind; approver_group_id: string | null; approver_user_id: string | null; min_amount: string | null }[];
};

export type PersonRef = { user_id: string; display_name: string };

export type RouteStep = {
  step_no: number;
  label: string;
  approver_kind: ApproverKind;
  status: "waiting" | "pending" | "approved" | "returned" | "rejected" | "skipped" | "cancelled";
  skip_reason: string | null;
  approvers: PersonRef[];
  acted_by: PersonRef | null;
  acted_at: string | null;
  comment: string | null;
};

export type RoutePreview = { steps: RouteStep[]; auto_approve: boolean; error: string | null };

export type RequestListItem = {
  id: string;
  request_number: string | null;
  type_name: string;
  type_code: string;
  title: string;
  applicant_id: string;
  applicant_name: string;
  amount: string | null;
  status: RequestStatus;
  current_step_label: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  updated_at: string;
  is_my_turn: boolean;
};

export type RequestEvent = {
  id: number;
  action: "submit" | "resubmit" | "approve" | "return" | "reject" | "withdraw" | "comment" | "skip" | "complete";
  round: number | null;
  step_no: number | null;
  step_label: string | null;
  actor: PersonRef | null;
  comment: string | null;
  created_at: string;
};

export type RequestAttachment = {
  id: string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by_name: string | null;
  created_at: string;
};

export type RequestDetail = RequestListItem & {
  request_type_id: string;
  form_schema: FormField[];
  form_data: Record<string, string>;
  amount_mode: "required" | "optional" | "none";
  amount_label: string;
  round: number;
  steps: RouteStep[];
  events: RequestEvent[];
  attachments: RequestAttachment[];
  permissions: {
    can_edit: boolean;
    can_submit: boolean;
    can_delete: boolean;
    can_withdraw: boolean;
    can_act: boolean;
    can_comment: boolean;
  };
};

export type RequestInput = {
  request_type_id: string;
  title: string;
  form_data: Record<string, string>;
  amount: string | null;
};

export type UploadFile = { file_name: string; content_type: string | null; content_base64: string };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// 開発用の利用者の切り替え(AUTH_MODE=dev のときだけ API が読む)。選んだ利用者はこのブラウザに覚えておく
const DEV_USER_KEY = "sme.devUserId";
let devUserId: string | null = (() => {
  try {
    return localStorage.getItem(DEV_USER_KEY);
  } catch {
    return null;
  }
})();

export function getDevUser(): string | null {
  return devUserId;
}

export function setDevUser(id: string | null): void {
  devUserId = id;
  try {
    if (id) localStorage.setItem(DEV_USER_KEY, id);
    else localStorage.removeItem(DEV_USER_KEY);
  } catch {
    // 保存できなくても、この画面を開いているあいだは切り替えが効く
  }
}

async function send(path: string, init: RequestInit = {}, tenantId?: string): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (tenantId) headers.set("X-Tenant-Id", tenantId);
  if (devUserId) headers.set("X-Dev-User-Id", devUserId);

  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "APIに接続できませんでした。docker compose で api が起動しているか確認してください。");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, errorMessage(res.status, body?.detail));
  }
  return res;
}

function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (status === 422 && Array.isArray(detail)) {
    // 入力チェックで日本語の理由を返しているときは、それをそのまま出す
    const msg = (detail[0] as { msg?: unknown } | undefined)?.msg;
    if (typeof msg === "string" && /[ぁ-んァ-ヶ一-龠]/.test(msg)) return msg.replace(/^Value error, /, "");
    return "入力内容に誤りがあります。品名・数量・単価を確認してください。";
  }
  return `エラーが発生しました(${status})`;
}

async function json<T>(path: string, init: RequestInit = {}, tenantId?: string): Promise<T> {
  const res = await send(path, init, tenantId);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  myTenants: () => json<Tenant[]>("/api/me/tenants"),

  session: () => json<Session>("/api/me"),
  members: (t: string, includeInactive = false) =>
    json<Member[]>(`/api/members${includeInactive ? "?include_inactive=true" : ""}`, {}, t),
  myProfile: (t: string) => json<Member>("/api/members/me", {}, t),
  createMember: (t: string, input: MemberInput) =>
    json<Member>("/api/members", { method: "POST", body: JSON.stringify(input) }, t),
  updateMember: (t: string, id: string, input: MemberInput) =>
    json<Member>(`/api/members/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),
  approverGroups: (t: string) => json<ApproverGroup[]>("/api/approver-groups", {}, t),
  createApproverGroup: (t: string, body: { name: string; user_ids: string[] }) =>
    json<ApproverGroup>("/api/approver-groups", { method: "POST", body: JSON.stringify(body) }, t),
  updateApproverGroup: (t: string, id: string, body: { name: string; user_ids: string[] }) =>
    json<ApproverGroup>(`/api/approver-groups/${id}`, { method: "PUT", body: JSON.stringify(body) }, t),
  deleteApproverGroup: (t: string, id: string) => json<void>(`/api/approver-groups/${id}`, { method: "DELETE" }, t),

  requestTypes: (t: string, includeInactive = false) =>
    json<RequestType[]>(`/api/request-types${includeInactive ? "?include_inactive=true" : ""}`, {}, t),
  updateRequestType: (t: string, id: string, body: RequestTypeInput) =>
    json<RequestType>(`/api/request-types/${id}`, { method: "PUT", body: JSON.stringify(body) }, t),
  routePreview: (t: string, requestTypeId: string, amount: string | null, signal?: AbortSignal) =>
    json<RoutePreview>(
      "/api/requests/route-preview",
      { method: "POST", body: JSON.stringify({ request_type_id: requestTypeId, amount }), signal },
      t,
    ),
  requests: (t: string, box: RequestBox, status?: RequestStatus) =>
    json<RequestListItem[]>(`/api/requests?box=${box}${status ? `&status_filter=${status}` : ""}`, {}, t),
  requestSummary: (t: string) => json<{ todo: number; returned: number }>("/api/requests/summary", {}, t),
  request: (t: string, id: string) => json<RequestDetail>(`/api/requests/${id}`, {}, t),
  createRequest: (t: string, input: RequestInput) =>
    json<RequestDetail>("/api/requests", { method: "POST", body: JSON.stringify(input) }, t),
  updateRequest: (t: string, id: string, input: RequestInput) =>
    json<RequestDetail>(`/api/requests/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),
  deleteRequest: (t: string, id: string) => json<void>(`/api/requests/${id}`, { method: "DELETE" }, t),
  requestAction: (
    t: string,
    id: string,
    action: "submit" | "approve" | "return" | "reject" | "withdraw" | "comments",
    comment: string | null = null,
  ) => json<RequestDetail>(`/api/requests/${id}/${action}`, { method: "POST", body: JSON.stringify({ comment }) }, t),
  addAttachment: (t: string, id: string, file: UploadFile) =>
    json<RequestDetail>(`/api/requests/${id}/attachments`, { method: "POST", body: JSON.stringify(file) }, t),
  deleteAttachment: (t: string, id: string, attachmentId: string) =>
    json<RequestDetail>(`/api/requests/${id}/attachments/${attachmentId}`, { method: "DELETE" }, t),
  openAttachment: (t: string, id: string, attachment: RequestAttachment) =>
    openPdf(t, `/api/requests/${id}/attachments/${attachment.id}`, attachment.file_name),

  importSettings: (t: string) => json<ImportSettings>("/api/imports/settings", {}, t),
  updateImportSettings: (t: string, enabled: boolean) =>
    json<ImportSettings>("/api/imports/settings", { method: "PUT", body: JSON.stringify({ enabled }) }, t),
  imports: (t: string, filter: ImportFilter) => json<ImportListItem[]>(`/api/imports?filter=${filter}`, {}, t),
  importDetail: (t: string, id: string) => json<ImportDetail>(`/api/imports/${id}`, {}, t),
  uploadImport: (t: string, file: { file_name: string; content_base64: string }) =>
    json<ImportDetail>("/api/imports", { method: "POST", body: JSON.stringify(file) }, t),
  saveImportDraft: (t: string, id: string, draft: ImportDraft) =>
    json<ImportDetail>(`/api/imports/${id}/draft`, { method: "PUT", body: JSON.stringify(draft) }, t),
  confirmImport: (t: string, id: string, draft: ImportDraft, acknowledged: string[]) =>
    json<ImportDetail>(`/api/imports/${id}/confirm`, { method: "POST", body: JSON.stringify({ draft, acknowledged }) }, t),
  retryImport: (t: string, id: string, asOrder = false) =>
    json<ImportDetail>(`/api/imports/${id}/retry`, { method: "POST", body: JSON.stringify({ as_order: asOrder }) }, t),
  discardImport: (t: string, id: string, reason: string | null) =>
    json<ImportDetail>(`/api/imports/${id}/discard`, { method: "POST", body: JSON.stringify({ reason }) }, t),
  /** 取り込んだPDFを画面の中に表示するための URL(使い終わったら URL.revokeObjectURL する) */
  importPdfUrl: async (t: string, id: string) => URL.createObjectURL(await (await send(`/api/imports/${id}/pdf`, {}, t)).blob()),

  customers: (t: string, includeInactive = false) =>
    json<Customer[]>(`/api/customers${includeInactive ? "?include_inactive=true" : ""}`, {}, t),
  createCustomer: (t: string, input: CustomerInput) =>
    json<Customer>("/api/customers", { method: "POST", body: JSON.stringify(input) }, t),
  updateCustomer: (t: string, id: string, input: CustomerInput) =>
    json<Customer>(`/api/customers/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),

  items: (t: string, includeInactive = false) =>
    json<Item[]>(`/api/items${includeInactive ? "?include_inactive=true" : ""}`, {}, t),
  createItem: (t: string, input: ItemInput) => json<Item>("/api/items", { method: "POST", body: JSON.stringify(input) }, t),
  updateItem: (t: string, id: string, input: ItemInput) =>
    json<Item>(`/api/items/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),

  /** データ一式(CSV と控えPDF)を ZIP でダウンロードする */
  downloadExport: (t: string) => download(t, "/api/export", "export.zip"),

  journalSettings: (t: string) => json<JournalSettings>("/api/journal/settings", {}, t),
  updateJournalSettings: (t: string, body: JournalSettingsInput) =>
    json<JournalSettings>("/api/journal/settings", { method: "PUT", body: JSON.stringify(body) }, t),
  journalPending: (t: string, from: string, to: string) =>
    json<JournalPreview>(`/api/journal/pending?date_from=${from}&date_to=${to}`, {}, t),
  createJournalExport: (t: string, from: string, to: string) =>
    json<JournalExport>("/api/journal/exports", { method: "POST", body: JSON.stringify({ date_from: from, date_to: to }) }, t),
  journalExports: (t: string) => json<JournalExport[]>("/api/journal/exports", {}, t),
  deleteJournalExport: (t: string, id: string) => json<void>(`/api/journal/exports/${id}`, { method: "DELETE" }, t),
  /** 仕訳の CSV をダウンロードする。Shift_JIS にない文字を置き換えた数を返す */
  downloadJournal: async (t: string, id: string) => {
    const res = await download(t, `/api/journal/exports/${id}/file`, "journal.csv");
    return Number(res.headers.get("X-Replaced-Characters") ?? "0");
  },

  quotes: (t: string, filter: QuoteFilter) => json<QuoteListItem[]>(`/api/quotes?filter=${filter}`, {}, t),
  quote: (t: string, id: string) => json<Quote>(`/api/quotes/${id}`, {}, t),
  calculate: (
    t: string,
    lines: Pick<LineInput, "quantity" | "unit_price" | "tax_category">[],
    onDate: string | null,
    signal?: AbortSignal,
    withholding = false,
  ) =>
    json<Calculation>(
      "/api/calculate",
      { method: "POST", body: JSON.stringify({ lines, on_date: onDate, withholding }), signal },
      t,
    ),
  createQuote: (t: string, input: QuoteInput) =>
    json<Quote>("/api/quotes", { method: "POST", body: JSON.stringify(input) }, t),
  updateQuote: (t: string, id: string, input: QuoteInput) =>
    json<Quote>(`/api/quotes/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),
  deleteQuote: (t: string, id: string) => json<void>(`/api/quotes/${id}`, { method: "DELETE" }, t),
  issueQuote: (t: string, id: string) => json<Quote>(`/api/quotes/${id}/issue`, { method: "POST" }, t),
  loseQuote: (t: string, id: string) => json<Quote>(`/api/quotes/${id}/lose`, { method: "POST" }, t),

  orders: (t: string, filter: OrderFilter) => json<OrderListItem[]>(`/api/orders?filter=${filter}`, {}, t),
  order: (t: string, id: string) => json<Order>(`/api/orders/${id}`, {}, t),
  createOrder: (t: string, input: OrderInput) =>
    json<Order>("/api/orders", { method: "POST", body: JSON.stringify(input) }, t),
  convertQuote: (t: string, quoteId: string) =>
    json<Order>(`/api/orders/from-quote/${quoteId}`, { method: "POST" }, t),
  updateOrder: (t: string, id: string, input: OrderInput) =>
    json<Order>(`/api/orders/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),
  deleteOrder: (t: string, id: string) => json<void>(`/api/orders/${id}`, { method: "DELETE" }, t),
  issueAcknowledgment: (t: string, id: string) =>
    json<Order>(`/api/orders/${id}/acknowledgment`, { method: "POST" }, t),

  invoices: (t: string, filter: InvoiceFilter) => json<InvoiceListItem[]>(`/api/invoices?filter=${filter}`, {}, t),
  invoice: (t: string, id: string) => json<Invoice>(`/api/invoices/${id}`, {}, t),
  createInvoice: (t: string, input: InvoiceInput) =>
    json<Invoice>("/api/invoices", { method: "POST", body: JSON.stringify(input) }, t),
  updateInvoice: (t: string, id: string, input: InvoiceInput) =>
    json<Invoice>(`/api/invoices/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),
  deleteInvoice: (t: string, id: string) => json<void>(`/api/invoices/${id}`, { method: "DELETE" }, t),
  invoiceFromOrder: (t: string, orderId: string) =>
    json<Invoice>(`/api/invoices/from-order/${orderId}`, { method: "POST" }, t),
  billableLines: (t: string, customerId: string, closingDate: string | null) =>
    json<Billable>(
      `/api/invoices/billable-lines?customer_id=${customerId}${closingDate ? `&closing_date=${closingDate}` : ""}`,
      {},
      t,
    ),
  invoiceFromOrderLines: (t: string, body: { customer_id: string; closing_date: string | null; order_line_ids: string[] }) =>
    json<Invoice>("/api/invoices/from-orders", { method: "POST", body: JSON.stringify(body) }, t),
  issueInvoice: (t: string, id: string) => json<Invoice>(`/api/invoices/${id}/issue`, { method: "POST" }, t),
  correctInvoice: (t: string, id: string) => json<Invoice>(`/api/invoices/${id}/corrections`, { method: "POST" }, t),
  voidInvoice: (t: string, id: string) => json<Invoice>(`/api/invoices/${id}/void`, { method: "POST" }, t),
  addPayment: (t: string, id: string, body: { paid_on: string; amount: string; memo: string | null }) =>
    json<Invoice>(`/api/invoices/${id}/payments`, { method: "POST", body: JSON.stringify(body) }, t),
  deletePayment: (t: string, id: string, paymentId: string) =>
    json<Invoice>(`/api/invoices/${id}/payments/${paymentId}`, { method: "DELETE" }, t),
  openInvoicePdf: (t: string, id: string, preopened?: Window | null) =>
    openPdf(t, `/api/invoices/${id}/pdf`, "請求書.pdf", preopened),

  openInvoices: (t: string, customerId: string) =>
    json<{ invoices: OpenInvoice[]; total_balance: string }>(`/api/receipts/open-invoices?customer_id=${customerId}`, {}, t),
  receipts: (t: string, customerId: string) => json<Receipt[]>(`/api/receipts?customer_id=${customerId}`, {}, t),
  createReceipt: (
    t: string,
    body: { customer_id: string; received_on: string; amount: string; memo: string | null; dry_run: boolean },
  ) => json<Receipt>("/api/receipts", { method: "POST", body: JSON.stringify(body) }, t),
  deleteReceipt: (t: string, id: string) => json<void>(`/api/receipts/${id}`, { method: "DELETE" }, t),

  bankFormats: (t: string) => json<BankFormat[]>("/api/bank/formats", {}, t),
  createBankFormat: (t: string, input: BankFormatInput) =>
    json<BankFormat>("/api/bank/formats", { method: "POST", body: JSON.stringify(input) }, t),
  updateBankFormat: (t: string, id: string, input: BankFormatInput) =>
    json<BankFormat>(`/api/bank/formats/${id}`, { method: "PUT", body: JSON.stringify(input) }, t),
  deleteBankFormat: (t: string, id: string) => json<void>(`/api/bank/formats/${id}`, { method: "DELETE" }, t),
  previewBankFile: (t: string, file: { filename: string; content_base64: string }, encoding: Encoding) =>
    json<BankPreview>("/api/bank/preview", { method: "POST", body: JSON.stringify({ ...file, encoding }) }, t),
  importBankFile: (t: string, formatId: string, file: { filename: string; content_base64: string }) =>
    json<BankImportResult>("/api/bank/imports", { method: "POST", body: JSON.stringify({ ...file, format_id: formatId }) }, t),
  bankImports: (t: string) => json<BankImportSummary[]>("/api/bank/imports", {}, t),
  deleteBankImport: (t: string, id: string) => json<void>(`/api/bank/imports/${id}`, { method: "DELETE" }, t),
  bankStatements: (t: string, status: StatementStatus) => json<StatementsResponse>(`/api/bank/statements?status=${status}`, {}, t),
  matchStatement: (
    t: string,
    id: string,
    body: { customer_id: string; invoice_ids: string[]; fee_amount: string; remember_payer: boolean },
  ) => json<BankStatement>(`/api/bank/statements/${id}/match`, { method: "POST", body: JSON.stringify(body) }, t),
  unmatchStatement: (t: string, id: string) => json<BankStatement>(`/api/bank/statements/${id}/match`, { method: "DELETE" }, t),
  ignoreStatement: (t: string, id: string, reason: string | null) =>
    json<BankStatement>(`/api/bank/statements/${id}/ignore`, { method: "POST", body: JSON.stringify({ reason }) }, t),
  unignoreStatement: (t: string, id: string) => json<BankStatement>(`/api/bank/statements/${id}/ignore`, { method: "DELETE" }, t),

  searchDocuments: (t: string, query: DocumentQuery) => {
    const params = new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][]);
    return json<IssuedDocument[]>(`/api/documents?${params}`, {}, t);
  },
  verifyDocument: (t: string, id: string) =>
    json<{ ok: boolean; sha256_saved: string; sha256_now: string | null; message: string }>(`/api/documents/${id}/verify`, {}, t),
  openDocumentPdf: (t: string, id: string) => openPdf(t, `/api/documents/${id}/pdf`, "控え.pdf"),

  company: (t: string) => json<Company>("/api/company", {}, t),
  updateCompany: (t: string, body: { profile: CompanyProfile; terms: PaymentTerms; bank_fee_limit?: number }) =>
    json<Company>("/api/company", { method: "PUT", body: JSON.stringify(body) }, t),

  openQuotePdf: (t: string, id: string, preopened?: Window | null) =>
    openPdf(t, `/api/quotes/${id}/pdf`, "見積書.pdf", preopened),
  openAcknowledgmentPdf: (t: string, id: string, options: { preview?: boolean; version?: number } = {}, preopened?: Window | null) => {
    const query = options.preview ? "?preview=true" : options.version ? `?version=${options.version}` : "";
    return openPdf(t, `/api/orders/${id}/acknowledgment/pdf${query}`, "注文請書.pdf", preopened);
  },
};

/**
 * PDF を新しいタブで開く(ヘッダーが必要なので、取得してから表示する)。
 * 保存などを待ってから開くときは、クリック直後に開いたタブを preopened に渡す
 */
async function openPdf(t: string, path: string, filename: string, preopened?: Window | null): Promise<void> {
  const tab = preopened === undefined ? window.open("", "_blank") : preopened;
  if (tab) tab.document.title = path.includes("/attachments/") ? "ファイルを開いています…" : "PDFを作成しています…";
  try {
    const blob = await (await send(path, {}, t)).blob();
    const url = URL.createObjectURL(blob);
    if (tab) {
      tab.location.href = url;
    } else {
      // ポップアップがブロックされたときはダウンロードにする
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (e) {
    tab?.close();
    throw e;
  }
}

/** ファイルをダウンロードする。ファイル名は Content-Disposition に従う */
async function download(t: string, path: string, fallback: string): Promise<Response> {
  const res = await send(path, {}, t);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = exportFilename(res.headers.get("Content-Disposition")) ?? fallback;
  document.body.append(a); // 画面に置かないとファイル名が使われないブラウザがある
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return res;
}

/** Content-Disposition から日本語のファイル名(filename*)を取り出す */
function exportFilename(header: string | null): string | null {
  const m = header?.match(/filename\*=UTF-8''([^;]+)/i);
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return null;
  }
}
