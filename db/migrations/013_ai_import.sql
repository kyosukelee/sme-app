-- =====================================================================
-- AI による注文書の取り込み
--   ・顧客から届いた注文書PDFを AI で読み取り、受注の下書きを作る。人が確認して確定すると受注になる
--   ・入金消込と同じ「取り込み → 候補 → 確定」の形。AI は読み取りだけで、金額は自社の計算で決める
--   ・会社ごとにオン・オフできる(初期値はオフ。オンにすると PDF の内容を AI のサービスに送る)
--   ・取り込んだ PDF と AI の読み取り結果は消さない(破棄しても記録として残す)
-- =====================================================================
BEGIN;

ALTER TABLE app.tenant_settings
  ADD COLUMN ai_import_enabled boolean NOT NULL DEFAULT false;

-- 取引先の「注文書上の名前」。確定すると登録され、次からは完全一致で候補に出す(振込名義と同じ形)
ALTER TABLE app.customers
  ADD COLUMN document_names text[] NOT NULL DEFAULT '{}';

-- 取り込み1件 = PDF 1ファイル
CREATE TABLE app.document_imports (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid        NOT NULL REFERENCES app.tenants (id),
  kind            text        NOT NULL DEFAULT 'order' CHECK (kind IN ('order')),
  status          text        NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'processing', 'review', 'failed', 'confirmed', 'discarded')),
  file_name       text        NOT NULL CHECK (length(file_name) BETWEEN 1 AND 200),
  content_type    text        NOT NULL,
  size_bytes      integer     NOT NULL CHECK (size_bytes BETWEEN 1 AND 10485760),
  sha256          text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  storage_path    text        NOT NULL,
  page_count      smallint,
  extract_method  text        CHECK (extract_method IN ('text', 'vision')),
  extracted       jsonb,       -- AI が返した値そのまま。書き込んだら変えない
  draft           jsonb,       -- 確認画面で人が直している内容
  checks          jsonb,       -- 直近の検証結果(エラー・警告)
  provider        text,
  model           text,
  prompt_version  text,
  duration_ms     integer,
  input_tokens    integer,
  output_tokens   integer,
  error_message   text,
  discard_reason  text,
  created_by      uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  confirmed_by    uuid,
  confirmed_at    timestamptz,
  UNIQUE (tenant_id, id)
);
-- 同じ PDF を二重に取り込まない(破棄したものは除く)
CREATE UNIQUE INDEX document_imports_sha256_once ON app.document_imports (tenant_id, sha256)
  WHERE status <> 'discarded';
CREATE INDEX document_imports_list_idx ON app.document_imports (tenant_id, created_at DESC);

-- 確定したときの「AI の値 → 人が確定した値」。違った項目だけを記録する(精度の集計に使う)
CREATE TABLE app.document_import_corrections (
  id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id    uuid        NOT NULL,
  import_id    uuid        NOT NULL,
  field        text        NOT NULL,   -- 例: customer_order_ref, lines[2].quantity
  ai_value     text,
  final_value  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, import_id) REFERENCES app.document_imports (tenant_id, id)
);
CREATE INDEX document_import_corrections_import_idx ON app.document_import_corrections (tenant_id, import_id);

-- 受注: お客様の注文番号と、取り込み元
ALTER TABLE app.orders
  ADD COLUMN customer_order_ref text CHECK (length(customer_order_ref) BETWEEN 1 AND 50),
  ADD COLUMN source_import_id   uuid,
  ADD FOREIGN KEY (tenant_id, source_import_id) REFERENCES app.document_imports (tenant_id, id);
-- 同じ取引先の同じ注文番号は1件だけ(二重登録を防ぐ)
CREATE UNIQUE INDEX orders_customer_order_ref_once ON app.orders (tenant_id, customer_id, customer_order_ref)
  WHERE customer_order_ref IS NOT NULL;
-- 1つの取り込みから作れる受注は1件だけ
CREATE UNIQUE INDEX orders_source_import_once ON app.orders (tenant_id, source_import_id)
  WHERE source_import_id IS NOT NULL;

-- 取り込み: AI の読み取り結果は書き換えさせない。確定・破棄した取り込みは変えさせない。削除はさせない
CREATE FUNCTION app.guard_document_import() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION '取り込んだ書類の記録は削除できません。不要なら破棄してください';
  END IF;

  IF OLD.status IN ('confirmed', 'discarded') THEN
    RAISE EXCEPTION '確定・破棄した取り込みは変更できません';
  END IF;

  IF OLD.extracted IS NOT NULL AND NEW.extracted IS DISTINCT FROM OLD.extracted THEN
    RAISE EXCEPTION 'AI の読み取り結果は書き換えられません';
  END IF;

  IF (NEW.tenant_id, NEW.kind, NEW.file_name, NEW.content_type, NEW.size_bytes, NEW.sha256, NEW.storage_path, NEW.created_by)
     IS DISTINCT FROM
     (OLD.tenant_id, OLD.kind, OLD.file_name, OLD.content_type, OLD.size_bytes, OLD.sha256, OLD.storage_path, OLD.created_by) THEN
    RAISE EXCEPTION '取り込んだファイルの情報は変更できません';
  END IF;

  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'queued'     AND NEW.status IN ('processing', 'failed', 'discarded'))
    OR (OLD.status = 'processing' AND NEW.status IN ('review', 'failed'))
    OR (OLD.status = 'failed'     AND NEW.status IN ('queued', 'discarded'))
    OR (OLD.status = 'review'     AND NEW.status IN ('confirmed', 'discarded'))
  ) THEN
    RAISE EXCEPTION '取り込みの状態を % から % には変更できません', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER guard_document_import BEFORE UPDATE OR DELETE ON app.document_imports
  FOR EACH ROW EXECUTE FUNCTION app.guard_document_import();
CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.document_imports
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['document_imports', 'document_import_corrections']
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I
                    USING (tenant_id = app.current_tenant_id())
                    WITH CHECK (tenant_id = app.current_tenant_id())', t);
  END LOOP;
END $$;

-- 取り込みは削除させない(トリガーでも止める)。修正の記録は追加だけ
GRANT SELECT, INSERT, UPDATE ON app.document_imports TO app_user;
GRANT SELECT, INSERT ON app.document_import_corrections TO app_user;
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.document_imports
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();

COMMIT;
