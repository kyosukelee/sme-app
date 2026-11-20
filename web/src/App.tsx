import { useEffect, useState } from "react";
import { api, getDevUser, setDevUser, type Member, type Tenant } from "./api";
import { CompanyProvider } from "./company";
import CompanySettings from "./pages/CompanySettings";
import Customers from "./pages/Customers";
import DocumentSearch from "./pages/DocumentSearch";
import InvoiceClosing from "./pages/InvoiceClosing";
import InvoiceList from "./pages/InvoiceList";
import InvoicePage from "./pages/InvoicePage";
import Items from "./pages/Items";
import Journal from "./pages/Journal";
import Members from "./pages/Members";
import OrderList from "./pages/OrderList";
import OrderPage from "./pages/OrderPage";
import QuoteEditor from "./pages/QuoteEditor";
import QuoteList from "./pages/QuoteList";
import QuotePage from "./pages/QuotePage";
import Receipts from "./pages/Receipts";
import Reconcile from "./pages/Reconcile";
import RequestEditor from "./pages/requests/RequestEditor";
import RequestList from "./pages/requests/RequestList";
import RequestPage from "./pages/requests/RequestPage";
import RequestTypes from "./pages/requests/RequestTypes";
import { navigate, useHashPath } from "./router";

type NavItem = { label: string; href?: string; section?: string; count?: "todo" };

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "書類",
    items: [
      { label: "見積", href: "#/quotes", section: "/quotes" },
      { label: "受注", href: "#/orders", section: "/orders" },
      { label: "請求", href: "#/invoices", section: "/invoices" },
      { label: "入金", href: "#/receipts", section: "/receipts" },
      { label: "入金消込", href: "#/reconcile", section: "/reconcile" },
      { label: "仕訳の出力", href: "#/journal", section: "/journal" },
      { label: "書類の検索", href: "#/documents", section: "/documents" },
    ],
  },
  {
    group: "ワークフロー",
    items: [{ label: "申請・承認", href: "#/requests", section: "/requests", count: "todo" }],
  },
  {
    group: "マスタ",
    items: [{ label: "取引先", href: "#/customers", section: "/customers" }, { label: "商品・サービス", href: "#/items", section: "/items" }],
  },
  {
    group: "設定",
    items: [
      { label: "自社情報", href: "#/settings/company", section: "/settings/company" },
      { label: "社員・担当役割", href: "#/settings/members", section: "/settings/members" },
      { label: "申請種別・承認ルート", href: "#/settings/request-types", section: "/settings/request-types" },
    ],
  },
];

export default function App() {
  const path = useHashPath();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [devMode, setDevMode] = useState(false);
  const [devUser, setDevUserState] = useState(getDevUser());
  const [me, setMe] = useState<Member | null>(null);
  const [people, setPeople] = useState<Member[]>([]); // 開発用の切り替えの候補(選んだ会社の社員)
  const [todo, setTodo] = useState(0); // 自分の承認待ちの件数(メニューに出す)
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .session()
      .then((s) => {
        setDevMode(s.auth_mode === "dev");
        if (s.auth_mode !== "dev" && getDevUser()) {
          setDevUser(null); // 本番では開発用の切り替えを使わない
          setDevUserState(null);
        }
      })
      .catch(() => setDevMode(false));
  }, []);

  useEffect(() => {
    setError("");
    api
      .myTenants()
      .then((list) => {
        setTenants(list);
        setTenantId((current) => (list.some((t) => t.id === current) ? current : (list[0]?.id ?? "")));
        if (list.length === 0) setError("利用できる会社がありません。");
      })
      .catch((e: Error) => setError(e.message));
  }, [devUser]);

  useEffect(() => {
    setMe(null);
    if (!tenantId) return;
    api.myProfile(tenantId).then(setMe).catch(() => setMe(null));
    if (devMode) api.members(tenantId).then(setPeople).catch(() => setPeople([]));
  }, [tenantId, devUser, devMode]);

  // 承認待ちの件数は、画面を移るたびと、承認・申請をしたときに取り直す
  useEffect(() => {
    if (!tenantId) return;
    const refresh = () =>
      api
        .requestSummary(tenantId)
        .then((s) => setTodo(s.todo))
        .catch(() => setTodo(0));
    refresh();
    window.addEventListener("requests-changed", refresh);
    return () => window.removeEventListener("requests-changed", refresh);
  }, [tenantId, devUser, path]);

  function backToList() {
    // 書類1件の画面は前の会社(利用者)のものなので、一覧に戻る
    if (path.startsWith("/quotes/")) navigate("/quotes");
    if (path.startsWith("/orders/")) navigate("/orders");
    if (path.startsWith("/invoices/")) navigate("/invoices");
    if (path.startsWith("/requests/")) navigate("/requests");
  }

  function switchTenant(id: string) {
    setTenantId(id);
    backToList();
  }

  function switchUser(id: string) {
    setDevUser(id || null);
    setDevUserState(id || null);
    backToList();
  }

  const tenant = tenants.find((t) => t.id === tenantId);

  return (
    <div className="shell">
      <aside className="sidebar">
        <p className="product">見積・受注・請求</p>
        <nav aria-label="メニュー">
          {NAV.map((section) => (
            <div key={section.group} className="nav-group">
              <p className="nav-heading">{section.group}</p>
              <ul>
                {section.items.map((item) => (
                  <li key={item.label}>
                    {item.href ? (
                      <a
                        className={`nav-link${path.startsWith(item.section!) ? " is-active" : ""}`}
                        href={item.href}
                        aria-current={path.startsWith(item.section!) ? "page" : undefined}
                      >
                        {item.label}
                        {item.count === "todo" && todo > 0 && (
                          <span className="nav-count" aria-label={`承認待ち${todo}件`}>
                            {todo}
                          </span>
                        )}
                      </a>
                    ) : (
                      <span className="nav-link is-pending">
                        {item.label}
                        <small>準備中</small>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </aside>

      <div className="workspace">
        <header className="topbar">
          {tenant && (
            <div className="company">
              <span className="seal" aria-hidden="true">
                {tenant.name.slice(0, 1)}
              </span>
              <div>
                <p className="company-name">{tenant.name}</p>
                <p className="company-role">
                  {me ? `${me.display_name}(${me.role === "admin" ? "管理者" : "一般"})` : tenant.role === "admin" ? "管理者" : "一般"}
                  としてログイン中
                </p>
              </div>
            </div>
          )}
          <div className="switchers">
          {devMode && people.length > 0 && (
            <label className="switcher dev-switcher">
              <span>利用者を切り替える(開発用)</span>
              <select value={me?.user_id ?? ""} onChange={(e) => switchUser(e.target.value)}>
                {people.map((p) => (
                  <option key={p.user_id} value={p.user_id}>
                    {p.display_name}
                    {p.role === "admin" ? "(管理者)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {tenants.length > 1 && (
            <label className="switcher">
              <span>会社を切り替える</span>
              <select value={tenantId} onChange={(e) => switchTenant(e.target.value)}>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          </div>
        </header>

        <main className="content">
          {error && (
            <p className="alert" role="alert">
              {error}
            </p>
          )}
          {tenantId && (
            <CompanyProvider key={`${tenantId}:${devUser ?? ""}`} tenantId={tenantId}>
              <Page path={path} tenantId={tenantId} isAdmin={me?.role === "admin"} todo={todo} />
            </CompanyProvider>
          )}
        </main>
      </div>
    </div>
  );
}

function Page({ path, tenantId, isAdmin, todo }: { path: string; tenantId: string; isAdmin: boolean; todo: number }) {
  if (path === "/requests" || path.startsWith("/requests?")) {
    const box = new URLSearchParams(path.split("?")[1] ?? "").get("box");
    const value = box === "todo" || box === "mine" || box === "all" ? box : todo > 0 ? "todo" : "mine";
    return <RequestList tenantId={tenantId} box={value} isAdmin={isAdmin} />;
  }
  if (path === "/requests/new" || path.startsWith("/requests/new?")) {
    const type = new URLSearchParams(path.split("?")[1] ?? "").get("type");
    return <RequestEditor key={`new-${tenantId}-${type ?? ""}`} tenantId={tenantId} initialType={type} />;
  }
  const requestEdit = path.match(/^\/requests\/([0-9a-f-]{36})\/edit$/);
  if (requestEdit) return <RequestEditor key={`edit-${requestEdit[1]}`} tenantId={tenantId} id={requestEdit[1]} />;
  const request = path.match(/^\/requests\/([0-9a-f-]{36})$/);
  if (request) return <RequestPage key={request[1]} tenantId={tenantId} id={request[1]} />;
  if (path === "/settings/request-types") return <RequestTypes tenantId={tenantId} />;
  if (path === "/customers") return <Customers tenantId={tenantId} />;
  if (path === "/items") return <Items tenantId={tenantId} />;
  if (path === "/settings/company") return <CompanySettings tenantId={tenantId} />;
  if (path === "/documents") return <DocumentSearch key={`documents-${tenantId}`} tenantId={tenantId} />;
  if (path === "/reconcile") return <Reconcile tenantId={tenantId} />;
  if (path === "/journal") return <Journal tenantId={tenantId} />;
  if (path === "/settings/members") return <Members tenantId={tenantId} />;
  if (path === "/receipts" || path.startsWith("/receipts?")) {
    const customer = new URLSearchParams(path.split("?")[1] ?? "").get("customer");
    return <Receipts key={`receipts-${tenantId}`} tenantId={tenantId} initialCustomerId={customer} />;
  }
  if (path === "/invoices") return <InvoiceList tenantId={tenantId} />;
  if (path === "/invoices/new") return <InvoicePage key={`new-${tenantId}`} tenantId={tenantId} />;
  if (path === "/invoices/closing") return <InvoiceClosing key={`closing-${tenantId}`} tenantId={tenantId} />;
  const invoice = path.match(/^\/invoices\/([0-9a-f-]{36})$/);
  if (invoice) return <InvoicePage tenantId={tenantId} id={invoice[1]} />;
  if (path === "/orders") return <OrderList tenantId={tenantId} />;
  if (path === "/orders/new") return <OrderPage key={`new-${tenantId}`} tenantId={tenantId} />;
  const order = path.match(/^\/orders\/([0-9a-f-]{36})$/);
  if (order) return <OrderPage tenantId={tenantId} id={order[1]} />;
  if (path === "/quotes/new") return <QuoteEditor key={`new-${tenantId}`} tenantId={tenantId} onIssued={() => {}} />;
  const match = path.match(/^\/quotes\/([0-9a-f-]{36})$/);
  if (match) return <QuotePage tenantId={tenantId} id={match[1]} />;
  return <QuoteList tenantId={tenantId} />;
}
