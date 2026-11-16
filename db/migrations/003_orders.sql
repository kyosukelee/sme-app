-- =====================================================================
-- 受注: 税率別集計の保存、請求済みの受注の保護、注文請書の控え
-- =====================================================================
BEGIN;

-- 受注の税率別集計(見積と同じ形)
CREATE TABLE app.order_tax_summaries (
  tenant_id       uuid             NOT NULL,
  order_id        uuid             NOT NULL,
  tax_category    app.tax_category NOT NULL,
  tax_rate        numeric(5,4)     NOT NULL,
  taxable_amount  numeric(15,0)    NOT NULL,
  tax_amount      numeric(15,0)    NOT NULL,
  PRIMARY KEY (order_id, tax_category, tax_rate),
  FOREIGN KEY (tenant_id, order_id) REFERENCES app.orders (tenant_id, id) ON DELETE CASCADE
);

ALTER TABLE app.order_tax_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.order_tax_summaries
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON app.order_tax_summaries TO app_user;

-- 1つの見積から作れる受注は1件だけ
CREATE UNIQUE INDEX orders_quote_once ON app.orders (tenant_id, quote_id) WHERE quote_id IS NOT NULL;

-- 明細の保護: 見積・請求は作成中だけ、受注は未請求のあいだだけ変更できる
CREATE OR REPLACE FUNCTION app.guard_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  r         jsonb := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  v_status  text;
  v_locked  boolean;
BEGIN
  IF TG_TABLE_NAME IN ('quote_lines', 'quote_tax_summaries') THEN
    SELECT status INTO v_status FROM app.quotes WHERE id = (r->>'quote_id')::uuid;
    v_locked := v_status IS NOT NULL AND v_status <> 'draft';
  ELSIF TG_TABLE_NAME IN ('order_lines', 'order_tax_summaries') THEN
    SELECT status INTO v_status FROM app.orders WHERE id = (r->>'order_id')::uuid;
    v_locked := v_status = 'billed';
  ELSE
    SELECT status INTO v_status FROM app.invoices WHERE id = (r->>'invoice_id')::uuid;
    v_locked := v_status IS NOT NULL AND v_status <> 'draft';
  END IF;

  -- 親が見つからない(書類ごと削除している途中)場合は通す
  IF v_locked THEN
    RAISE EXCEPTION '発行済み・請求済みの書類の明細は変更できません';
  END IF;

  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['order_lines', 'order_tax_summaries']
  LOOP
    EXECUTE format('CREATE TRIGGER guard_lines BEFORE INSERT OR UPDATE OR DELETE ON app.%I
                    FOR EACH ROW EXECUTE FUNCTION app.guard_lines()', t);
  END LOOP;
END $$;

-- 受注: 請求済みになったら内容を固定する。状態は 未請求⇔請求済 だけ(請求書の取消で戻る場合に備える)
CREATE FUNCTION app.guard_order_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'billed'
     AND (to_jsonb(NEW) - ARRAY['status', 'updated_at'])
         IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'updated_at']) THEN
    RAISE EXCEPTION '請求済みの受注は変更できません';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION app.guard_order_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'billed' THEN
    RAISE EXCEPTION '請求済みの受注は削除できません';
  END IF;
  RETURN OLD;
END $$;

CREATE TRIGGER guard_order_update BEFORE UPDATE ON app.orders FOR EACH ROW EXECUTE FUNCTION app.guard_order_update();
CREATE TRIGGER guard_order_delete BEFORE DELETE ON app.orders FOR EACH ROW EXECUTE FUNCTION app.guard_order_delete();

-- 見積の状態: 受注を削除したときに 受注済 → 発行済 へ戻せるようにする
CREATE OR REPLACE FUNCTION app.guard_quote_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'draft' THEN
    IF NEW.status NOT IN ('draft', 'issued') THEN
      RAISE EXCEPTION '作成中の見積は発行前に % にできません', NEW.status;
    END IF;
    RETURN NEW;
  END IF;

  IF (to_jsonb(NEW) - ARRAY['status', 'updated_at'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'updated_at']) THEN
    RAISE EXCEPTION '発行済みの見積は変更できません。新しい見積を作成してください';
  END IF;

  IF NEW.status <> OLD.status
     AND NOT ((OLD.status = 'issued' AND NEW.status IN ('ordered', 'lost'))
              OR (OLD.status = 'ordered' AND NEW.status = 'issued')) THEN
    RAISE EXCEPTION '見積の状態を % から % には変更できません', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END $$;

-- 注文請書の控えを発行控えに保存できるようにする
ALTER TABLE app.issued_documents DROP CONSTRAINT issued_documents_doc_type_check;
ALTER TABLE app.issued_documents
  ADD CONSTRAINT issued_documents_doc_type_check CHECK (doc_type IN ('quote', 'order', 'invoice'));

COMMIT;
