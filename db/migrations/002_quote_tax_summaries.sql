-- =====================================================================
-- 見積の税率別集計を保存する
--   発行後に会社設定(端数処理)が変わっても、発行時点の金額を再現できるようにする
-- =====================================================================
BEGIN;

CREATE TABLE app.quote_tax_summaries (
  tenant_id       uuid             NOT NULL,
  quote_id        uuid             NOT NULL,
  tax_category    app.tax_category NOT NULL,
  tax_rate        numeric(5,4)     NOT NULL,
  taxable_amount  numeric(15,0)    NOT NULL,
  tax_amount      numeric(15,0)    NOT NULL,
  PRIMARY KEY (quote_id, tax_category, tax_rate),
  FOREIGN KEY (tenant_id, quote_id) REFERENCES app.quotes (tenant_id, id) ON DELETE CASCADE
);

-- 明細の保護に見積の税率別集計を加える
CREATE OR REPLACE FUNCTION app.guard_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  r         jsonb := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  v_status  text;
BEGIN
  IF TG_TABLE_NAME IN ('quote_lines', 'quote_tax_summaries') THEN
    SELECT status INTO v_status FROM app.quotes WHERE id = (r->>'quote_id')::uuid;
  ELSE
    SELECT status INTO v_status FROM app.invoices WHERE id = (r->>'invoice_id')::uuid;
  END IF;

  -- 親が見つからない(作成中の書類ごと削除している途中)場合は通す
  IF v_status IS NOT NULL AND v_status <> 'draft' THEN
    RAISE EXCEPTION '発行済みの書類の明細は変更できません';
  END IF;

  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER guard_lines BEFORE INSERT OR UPDATE OR DELETE ON app.quote_tax_summaries
  FOR EACH ROW EXECUTE FUNCTION app.guard_lines();

ALTER TABLE app.quote_tax_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.quote_tax_summaries
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON app.quote_tax_summaries TO app_user;

COMMIT;
