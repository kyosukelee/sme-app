-- 会計ソフト向けの仕訳CSV
--   ・発行した請求書(売上)と入金を仕訳にして、勘定奉行・弥生会計・freee・マネーフォワード クラウド会計の
--     取り込み形式の CSV で出力する
--   ・一度出力したものは二重に出さない。出力後に訂正・取消・入金の取消をしたものは、次の出力で逆仕訳を出す
BEGIN;

-- 仕訳の設定(1社1行)。勘定科目と税区分は会計ソフト側の名前(勘定奉行はコード)に合わせて変えられる
CREATE TABLE app.journal_settings (
  tenant_id          uuid    PRIMARY KEY REFERENCES app.tenants (id),
  software           text    NOT NULL DEFAULT 'yayoi'
                             CHECK (software IN ('kanjo_bugyo', 'yayoi', 'freee', 'mf')),
  sales_date_basis   text    NOT NULL DEFAULT 'transaction'
                             CHECK (sales_date_basis IN ('transaction', 'issue')),  -- 取引日(締め請求は締め日) / 請求日
  customer_as_sub    boolean NOT NULL DEFAULT true,   -- 売掛金の補助科目に取引先名を入れる
  accounts           jsonb   NOT NULL DEFAULT '{}',   -- {"receivable": {"name": "売掛金", "code": "135", "sub": ""}, ...}
  tax_labels         jsonb   NOT NULL DEFAULT '{}',   -- {"yayoi": {"sales_standard": "課税売上込10%", ...}, ...}
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- 出力1回分
CREATE TABLE app.journal_exports (
  id           uuid      PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid      NOT NULL REFERENCES app.tenants (id),
  software     text      NOT NULL CHECK (software IN ('kanjo_bugyo', 'yayoi', 'freee', 'mf')),
  date_from    date      NOT NULL,
  date_to      date      NOT NULL CHECK (date_to >= date_from),
  entry_count  integer   NOT NULL DEFAULT 0,
  created_by   uuid,
  -- 出力の順番で「最後の出力」を決めるので、トランザクションの開始時刻ではなく実際の時刻を入れる
  created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (tenant_id, id)
);

-- 出力した仕訳。元になった請求書・入金ごとに、通常の仕訳(entry)と逆仕訳(reversal)を1回ずつまで出す。
-- 仕訳の中身(勘定の種類・税区分の種類・金額)を写しておき、元のデータが消えても逆仕訳を作れるようにする
CREATE TABLE app.journal_export_items (
  id           uuid      PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid      NOT NULL REFERENCES app.tenants (id),
  export_id    uuid      NOT NULL,
  source_type  text      NOT NULL CHECK (source_type IN ('invoice', 'receipt', 'payment')),
  source_id    uuid      NOT NULL,
  kind         text      NOT NULL CHECK (kind IN ('entry', 'reversal')),
  entry_date   date      NOT NULL,
  summary      text      NOT NULL,   -- 摘要
  partner      text,                 -- 取引先名
  lines        jsonb     NOT NULL,
  UNIQUE (tenant_id, source_type, source_id, kind),
  FOREIGN KEY (tenant_id, export_id) REFERENCES app.journal_exports (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX journal_export_items_export_idx ON app.journal_export_items (tenant_id, export_id);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['journal_settings', 'journal_exports', 'journal_export_items']
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I
                    USING (tenant_id = app.current_tenant_id())
                    WITH CHECK (tenant_id = app.current_tenant_id())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app.%I TO app_user', t);
  END LOOP;
END $$;

-- 設定と出力の操作は変更履歴に残す(仕訳の行そのものは量が多いので残さない)
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.journal_settings
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.journal_exports
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.journal_settings
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

COMMIT;
