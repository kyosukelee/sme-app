// API との通信。会社を選んだ後の通信には X-Tenant-Id ヘッダーを付ける
// 金額・数量は誤差が出ないよう、API とは文字列でやりとりする

export type Tenant = { id: string; name: string; role: "admin" | "member" };

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
  lines: (LineInput & { line_id: string | null; quote_line_id: string | null; delivered_on: string | null })[];
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

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function send(path: string, init: RequestInit = {}, tenantId?: string): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (tenantId) headers.set("X-Tenant-Id", tenantId);

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
  if (status === 422 && Array.isArray(detail)) return "入力内容に誤りがあります。品名・数量・単価を確認してください。";
  return `エラーが発生しました(${status})`;
}

async function json<T>(path: string, init: RequestInit = {}, tenantId?: string): Promise<T> {
  const res = await send(path, init, tenantId);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  myTenants: () => json<Tenant[]>("/api/me/tenants"),

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
  downloadExport: async (t: string) => {
    const res = await send("/api/export", {}, t);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = exportFilename(res.headers.get("Content-Disposition")) ?? "export.zip";
    document.body.append(a); // 画面に置かないとファイル名が使われないブラウザがある
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
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
  if (tab) tab.document.title = "PDFを作成しています…";
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
