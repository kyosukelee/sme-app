-- =====================================================================
-- 締め請求の繰越と、取引先ごとのまとめ入金
--   ・締め請求の請求書には、発行時点の「前回までの未入金」を繰越として印字する。
--     繰越は金額のお知らせで、インボイスの明細(今回の取引)には含めない
--   ・取引先がまとめて振り込んだ入金(receipts)を、古い請求書から順に割り当てる
-- =====================================================================
BEGIN;

-- 締め請求で作った請求書の締め日(締め請求以外は NULL)と、発行時に確定した繰越額
ALTER TABLE app.invoices
  ADD COLUMN closing_date date,
  ADD COLUMN carried_over_amount numeric(15,0) NOT NULL DEFAULT 0;

-- 繰越の内訳。発行時点の各請求書の残額を写しとして残す(あとで入金があっても書類の内容は変わらない)
CREATE TABLE app.invoice_carryovers (
  tenant_id          uuid          NOT NULL,
  invoice_id         uuid          NOT NULL,  -- 繰越を印字する請求書
  source_invoice_id  uuid          NOT NULL,  -- 未入金が残っていた請求書
  source_number      text          NOT NULL,
  source_version     smallint      NOT NULL,
  source_issue_date  date          NOT NULL,
  balance            numeric(15,0) NOT NULL CHECK (balance > 0),
  PRIMARY KEY (invoice_id, source_invoice_id),
  FOREIGN KEY (tenant_id, invoice_id)        REFERENCES app.invoices (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, source_invoice_id) REFERENCES app.invoices (tenant_id, id)
);

-- 発行後は繰越の内訳も変えられない(明細と同じ扱い)
CREATE TRIGGER guard_lines BEFORE INSERT OR UPDATE OR DELETE ON app.invoice_carryovers
  FOR EACH ROW EXECUTE FUNCTION app.guard_lines();

-- 取引先からの入金1回分。請求書への割り当ては payments に分けて記録する
CREATE TABLE app.receipts (
  id           uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid          NOT NULL REFERENCES app.tenants (id),
  customer_id  uuid          NOT NULL,
  received_on  date          NOT NULL,
  amount       numeric(15,0) NOT NULL CHECK (amount > 0),
  memo         text,
  created_by   uuid,
  created_at   timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES app.customers (tenant_id, id)
);
CREATE INDEX receipts_customer_idx ON app.receipts (tenant_id, customer_id, received_on DESC);

-- まとめ入金から割り当てた入金は、元の入金を取り消すと一緒に消える
ALTER TABLE app.payments
  ADD COLUMN receipt_id uuid,
  ADD FOREIGN KEY (tenant_id, receipt_id) REFERENCES app.receipts (tenant_id, id) ON DELETE CASCADE;
CREATE INDEX payments_receipt_idx ON app.payments (tenant_id, receipt_id);

CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.receipts
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['invoice_carryovers', 'receipts']
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I
                    USING (tenant_id = app.current_tenant_id())
                    WITH CHECK (tenant_id = app.current_tenant_id())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app.%I TO app_user', t);
  END LOOP;
END $$;

COMMIT;
