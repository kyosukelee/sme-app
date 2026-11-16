// いま選んでいる会社の事業形態と消費税の扱い。画面の表示(消費税の欄・源泉徴収の欄)を切り替えるのに使う
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Item } from "./api";

type CompanyInfo = {
  soleProprietor: boolean; // 個人事業主(源泉徴収を使える)
  taxExempt: boolean; // 免税事業者(消費税を表示しない)
  reload: () => void;
  items: Item[]; // 明細の品名の候補(使用中の商品・サービス)
  reloadItems: () => void;
};

const CompanyContext = createContext<CompanyInfo>({
  soleProprietor: false,
  taxExempt: false,
  reload: () => {},
  items: [],
  reloadItems: () => {},
});

export function CompanyProvider({ tenantId, children }: { tenantId: string; children: ReactNode }) {
  const [info, setInfo] = useState({ soleProprietor: false, taxExempt: false });

  const reload = useCallback(() => {
    api
      .company(tenantId)
      .then((c) =>
        setInfo({ soleProprietor: c.profile?.business_type === "sole_proprietor", taxExempt: c.profile?.tax_exempt ?? false }),
      )
      .catch(() => setInfo({ soleProprietor: false, taxExempt: false }));
  }, [tenantId]);

  const [items, setItems] = useState<Item[]>([]);
  const reloadItems = useCallback(() => {
    api
      .items(tenantId)
      .then(setItems)
      .catch(() => setItems([])); // 候補が出ないだけなので、エラーは表示しない
  }, [tenantId]);

  useEffect(reload, [reload]);
  useEffect(reloadItems, [reloadItems]);

  return <CompanyContext.Provider value={{ ...info, reload, items, reloadItems }}>{children}</CompanyContext.Provider>;
}

export const useCompany = () => useContext(CompanyContext);
