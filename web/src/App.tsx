import { useEffect, useState } from "react";
import { api, type Tenant } from "./api";
import { CompanyProvider } from "./company";
import CompanySettings from "./pages/CompanySettings";
import Customers from "./pages/Customers";
import DocumentSearch from "./pages/DocumentSearch";
import InvoiceClosing from "./pages/InvoiceClosing";
import InvoiceList from "./pages/InvoiceList";
import InvoicePage from "./pages/InvoicePage";
import Items from "./pages/Items";
import OrderList from "./pages/OrderList";
import OrderPage from "./pages/OrderPage";
import QuoteEditor from "./pages/QuoteEditor";
import QuoteList from "./pages/QuoteList";
import QuotePage from "./pages/QuotePage";
import Receipts from "./pages/Receipts";
import Reconcile from "./pages/Reconcile";
import { navigate, useHashPath } from "./router";

type NavItem = { label: string; href?: string; section?: string };

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "書類",
    items: [
      { label: "見積", href: "#/quotes", section: "/quotes" },
      { label: "受注", href: "#/orders", section: "/orders" },
      { label: "請求", href: "#/invoices", section: "/invoices" },
      { label: "入金", href: "#/receipts", section: "/receipts" },
      { label: "入金消込", href: "#/reconcile", section: "/reconcile" },
      { label: "書類の検索", href: "#/documents", section: "/documents" },
    ],
  },
  {
    group: "マスタ",
    items: [{ label: "取引先", href: "#/customers", section: "/customers" }, { label: "商品・サービス", href: "#/items", section: "/items" }],
  },
  {
    group: "設定",
    items: [{ label: "自社情報", href: "#/settings/company", section: "/settings" }],
  },
];

export default function App() {
  const path = useHashPath();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .myTenants()
      .then((list) => {
        setTenants(list);
        if (list.length > 0) setTenantId(list[0].id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  function switchTenant(id: string) {
    setTenantId(id);
    // 書類1件の画面は前の会社のものなので、一覧に戻る
    if (path.startsWith("/quotes/")) navigate("/quotes");
    if (path.startsWith("/orders/")) navigate("/orders");
    if (path.startsWith("/invoices/")) navigate("/invoices");
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
                <p className="company-role">{tenant.role === "admin" ? "管理者" : "一般"}としてログイン中</p>
              </div>
            </div>
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
        </header>

        <main className="content">
          {error && (
            <p className="alert" role="alert">
              {error}
            </p>
          )}
          {tenantId && (
            <CompanyProvider key={tenantId} tenantId={tenantId}>
              <Page path={path} tenantId={tenantId} />
            </CompanyProvider>
          )}
        </main>
      </div>
    </div>
  );
}

function Page({ path, tenantId }: { path: string; tenantId: string }) {
  if (path === "/customers") return <Customers tenantId={tenantId} />;
  if (path === "/items") return <Items tenantId={tenantId} />;
  if (path === "/settings/company") return <CompanySettings tenantId={tenantId} />;
  if (path === "/documents") return <DocumentSearch key={`documents-${tenantId}`} tenantId={tenantId} />;
  if (path === "/reconcile") return <Reconcile tenantId={tenantId} />;
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
