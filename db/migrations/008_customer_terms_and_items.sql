-- =====================================================================
-- 取引先ごとの締め日・支払日と、商品・サービスマスタ
--   ・取引先の締め日・支払月・支払日は「3つとも設定」か「3つとも空(会社設定に従う)」のどちらか
--   ・商品の変更履歴を残す(単価の変更をあとから確かめられるように)
-- =====================================================================
BEGIN;

ALTER TABLE app.customers
  ADD CONSTRAINT customers_terms_all_or_none CHECK (
    (closing_day IS NULL AND payment_month_offset IS NULL AND payment_day IS NULL)
    OR (closing_day IS NOT NULL AND payment_month_offset IS NOT NULL AND payment_day IS NOT NULL)
  );

CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.items
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.customers
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();

COMMIT;
