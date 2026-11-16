-- =====================================================================
-- 請求: 受注明細の請求状況、入金
-- =====================================================================
BEGIN;

-- 受注明細がどの請求書に載っているか。
-- 取消した請求書と、訂正版が出て置き換わった旧版は数えない。
-- security_invoker により、見る人の権限(RLS)で絞り込まれる
CREATE VIEW app.order_line_billing WITH (security_invoker = true) AS
SELECT il.tenant_id, il.order_line_id, il.invoice_id, i.status AS invoice_status
FROM app.invoice_lines il
JOIN app.invoices i ON i.id = il.invoice_id
WHERE il.order_line_id IS NOT NULL
  AND i.status <> 'void'
  AND i.superseded_at IS NULL;

GRANT SELECT ON app.order_line_billing TO app_user;

-- 同じ請求書の同じ版に、同じ受注明細を2回載せない
CREATE UNIQUE INDEX invoice_lines_order_line_once
  ON app.invoice_lines (invoice_id, order_line_id) WHERE order_line_id IS NOT NULL;

-- 入金は発行済みの請求書にだけ登録できる(取消・作成中には登録できない)
CREATE FUNCTION app.guard_payment() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_status text;
BEGIN
  SELECT status INTO v_status FROM app.invoices WHERE id = NEW.invoice_id;
  IF v_status NOT IN ('issued', 'paid') THEN
    RAISE EXCEPTION '入金を登録できるのは発行済みの請求書だけです';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER guard_payment BEFORE INSERT ON app.payments
  FOR EACH ROW EXECUTE FUNCTION app.guard_payment();

COMMIT;
