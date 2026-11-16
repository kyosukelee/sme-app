-- =====================================================================
-- 見積・受注・請求アプリ 初期スキーマ(MVP)
-- 対象: PostgreSQL 15 以上(Supabase と素の PostgreSQL の共通)
--
-- 方針
--   ・テーブルは app スキーマに置く。Supabase の自動API(public スキーマ)には
--     公開されないので、画面からDBを直接読むことはできない。
--   ・会社ごとの分離は二重にする。
--       1) 子テーブルの外部キーを (tenant_id, id) の複合キーにし、
--          他社のデータを参照できないようにする
--       2) RLS でセッション変数 app.tenant_id の会社だけを見せる
--   ・API はトランザクションの最初に次を実行してから処理する。
--       SET LOCAL app.tenant_id = '<会社ID>';
--       SET LOCAL app.user_id   = '<利用者ID>';
--   ・金額は円単位の numeric(15,0)、単価は numeric(15,2)、数量は numeric(12,3)。
--     計算は API(Python の Decimal)で行い、DB は結果を保存する。
-- =====================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------
-- 共通の型と関数
-- ---------------------------------------------------------------------

-- 税区分
--   standard=課税(標準税率) / reduced=課税(軽減税率) / exempt=非課税
--   non_taxable=不課税 / export=免税(輸出等)
CREATE DOMAIN app.tax_category AS text
  CHECK (VALUE IN ('standard', 'reduced', 'exempt', 'non_taxable', 'export'));

CREATE FUNCTION app.current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

CREATE FUNCTION app.current_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

CREATE FUNCTION app.set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ---------------------------------------------------------------------
-- 税区分と税率(全社共通の参照データ。tenant_id は持たない)
-- ---------------------------------------------------------------------
CREATE TABLE app.tax_categories (
  code        app.tax_category NOT NULL,
  valid_from  date             NOT NULL,
  name        text             NOT NULL,
  rate        numeric(5,4)     NOT NULL CHECK (rate >= 0 AND rate < 1),
  PRIMARY KEY (code, valid_from)
);

INSERT INTO app.tax_categories (code, valid_from, name, rate) VALUES
  ('standard',    '2019-10-01', '課税(標準税率)', 0.10),
  ('reduced',     '2019-10-01', '課税(軽減税率)', 0.08),
  ('exempt',      '2019-10-01', '非課税',         0),
  ('non_taxable', '2019-10-01', '不課税',         0),
  ('export',      '2019-10-01', '免税(輸出等)',   0);

-- 書類の日付時点で有効な税率を返す
CREATE FUNCTION app.tax_rate_on(p_code app.tax_category, p_date date)
RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT rate FROM app.tax_categories
  WHERE code = p_code AND valid_from <= p_date
  ORDER BY valid_from DESC
  LIMIT 1
$$;

-- ---------------------------------------------------------------------
-- 会社(テナント)と利用者
-- ---------------------------------------------------------------------
CREATE TABLE app.tenants (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text        NOT NULL,
  plan        text        NOT NULL DEFAULT 'saas' CHECK (plan IN ('saas', 'dedicated')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.memberships (
  tenant_id   uuid        NOT NULL REFERENCES app.tenants (id),
  user_id     uuid        NOT NULL,  -- 認証基盤の利用者ID。差し替えに備えて外部キーは張らない
  role        text        NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);
CREATE INDEX memberships_user_idx ON app.memberships (user_id);

-- 会社ごとの設定(1社1行)
CREATE TABLE app.tenant_settings (
  tenant_id             uuid     PRIMARY KEY REFERENCES app.tenants (id),
  rounding_mode         text     NOT NULL DEFAULT 'floor'
                                 CHECK (rounding_mode IN ('floor', 'half_up', 'ceil')),  -- 切り捨て/四捨五入/切り上げ
  numbering_period      text     NOT NULL DEFAULT 'year'
                                 CHECK (numbering_period IN ('year', 'month')),          -- 連番を年ごとか月ごとに振り直す
  number_digits         smallint NOT NULL DEFAULT 4 CHECK (number_digits BETWEEN 3 AND 8),
  quote_prefix          text     NOT NULL DEFAULT 'Q',
  order_prefix          text     NOT NULL DEFAULT 'O',
  invoice_prefix        text     NOT NULL DEFAULT 'INV',
  quote_valid_days      smallint NOT NULL DEFAULT 30 CHECK (quote_valid_days > 0),
  closing_day           smallint NOT NULL DEFAULT 31 CHECK (closing_day BETWEEN 1 AND 31),          -- 31 = 月末
  payment_month_offset  smallint NOT NULL DEFAULT 1  CHECK (payment_month_offset BETWEEN 0 AND 3),  -- 0=当月、1=翌月
  payment_day           smallint NOT NULL DEFAULT 31 CHECK (payment_day BETWEEN 1 AND 31),          -- 31 = 月末
  remarks_template      text,
  layout                text     NOT NULL DEFAULT 'standard'
                                 CHECK (layout IN ('standard', 'detailed', 'simple')),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

-- 自社情報(書類に印字する。1社1行)
CREATE TABLE app.company_profiles (
  tenant_id            uuid PRIMARY KEY REFERENCES app.tenants (id),
  name                 text NOT NULL,
  postal_code          text,
  address              text,
  phone                text,
  registration_number  text CHECK (registration_number ~ '^T[0-9]{13}$'),  -- 空欄 = 免税事業者など
  bank_account         text,  -- 振込先(表示用の文字列)
  logo_path            text,
  seal_path            text,
  updated_at           timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- マスタ
-- ---------------------------------------------------------------------
CREATE TABLE app.customers (
  id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid        NOT NULL REFERENCES app.tenants (id),
  name                  text        NOT NULL,
  honorific             text        NOT NULL DEFAULT '御中',
  postal_code           text,
  address               text,
  contact_name          text,
  email                 text,
  closing_day           smallint CHECK (closing_day BETWEEN 1 AND 31),           -- NULL = 会社設定に従う
  payment_month_offset  smallint CHECK (payment_month_offset BETWEEN 0 AND 3),
  payment_day           smallint CHECK (payment_day BETWEEN 1 AND 31),
  is_active             boolean     NOT NULL DEFAULT true,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id)
);

CREATE TABLE app.items (
  id            uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid             NOT NULL REFERENCES app.tenants (id),
  code          text,
  name          text             NOT NULL,
  unit          text,
  unit_price    numeric(15,2)    NOT NULL DEFAULT 0,
  tax_category  app.tax_category NOT NULL DEFAULT 'standard',
  is_active     boolean          NOT NULL DEFAULT true,
  created_at    timestamptz      NOT NULL DEFAULT now(),
  updated_at    timestamptz      NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, code)
);

-- ---------------------------------------------------------------------
-- 見積
-- ---------------------------------------------------------------------
CREATE TABLE app.quotes (
  id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid          NOT NULL REFERENCES app.tenants (id),
  quote_number        text,          -- 発行時に採番。作成中は NULL(画面には仮番号を表示)
  customer_id         uuid          NOT NULL,
  customer_name       text          NOT NULL,  -- 以下3項目は書類側に持つコピー
  customer_honorific  text          NOT NULL,
  customer_address    text,
  title               text,
  issue_date          date,
  valid_until         date,          -- 期限切れはこの日付で表示時に判定する
  status              text          NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft', 'issued', 'ordered', 'lost')),
  subtotal_amount     numeric(15,0) NOT NULL DEFAULT 0,
  tax_amount          numeric(15,0) NOT NULL DEFAULT 0,
  total_amount        numeric(15,0) NOT NULL DEFAULT 0,
  remarks             text,
  created_by          uuid,
  created_at          timestamptz   NOT NULL DEFAULT now(),
  updated_at          timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, quote_number),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES app.customers (tenant_id, id),
  CHECK (status = 'draft' OR (quote_number IS NOT NULL AND issue_date IS NOT NULL))
);

CREATE TABLE app.quote_lines (
  id            uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid             NOT NULL,
  quote_id      uuid             NOT NULL,
  line_no       smallint         NOT NULL,
  item_id       uuid,
  description   text             NOT NULL,
  quantity      numeric(12,3)    NOT NULL DEFAULT 1,
  unit          text,
  unit_price    numeric(15,2)    NOT NULL,
  amount        numeric(15,0)    NOT NULL,  -- 税抜金額。値引きはマイナス
  tax_category  app.tax_category NOT NULL,
  tax_rate      numeric(5,4)     NOT NULL,  -- 書類の日付時点の税率のコピー
  UNIQUE (tenant_id, id),
  UNIQUE (quote_id, line_no),
  FOREIGN KEY (tenant_id, quote_id) REFERENCES app.quotes (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, item_id)  REFERENCES app.items (tenant_id, id)
);

-- ---------------------------------------------------------------------
-- 受注
-- ---------------------------------------------------------------------
CREATE TABLE app.orders (
  id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid          NOT NULL REFERENCES app.tenants (id),
  order_number        text          NOT NULL,
  quote_id            uuid,          -- 見積なしの直接作成なら NULL
  customer_id         uuid          NOT NULL,
  customer_name       text          NOT NULL,
  customer_honorific  text          NOT NULL,
  customer_address    text,
  title               text,
  order_date          date          NOT NULL,
  delivery_due_date   date,          -- 納期(予定)
  status              text          NOT NULL DEFAULT 'open'
                                    CHECK (status IN ('open', 'billed')),  -- 未請求/請求済
  subtotal_amount     numeric(15,0) NOT NULL DEFAULT 0,
  tax_amount          numeric(15,0) NOT NULL DEFAULT 0,
  total_amount        numeric(15,0) NOT NULL DEFAULT 0,
  remarks             text,
  created_by          uuid,
  created_at          timestamptz   NOT NULL DEFAULT now(),
  updated_at          timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, order_number),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES app.customers (tenant_id, id),
  FOREIGN KEY (tenant_id, quote_id)    REFERENCES app.quotes (tenant_id, id)
);

CREATE TABLE app.order_lines (
  id             uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid             NOT NULL,
  order_id       uuid             NOT NULL,
  line_no        smallint         NOT NULL,
  quote_line_id  uuid,             -- 見積なしなら NULL
  item_id        uuid,
  description    text             NOT NULL,
  quantity       numeric(12,3)    NOT NULL DEFAULT 1,
  unit           text,
  unit_price     numeric(15,2)    NOT NULL,
  amount         numeric(15,0)    NOT NULL,
  tax_category   app.tax_category NOT NULL,
  tax_rate       numeric(5,4)     NOT NULL,
  delivered_on   date,             -- 実際の納品日。請求明細の取引日の元になる
  UNIQUE (tenant_id, id),
  UNIQUE (order_id, line_no),
  FOREIGN KEY (tenant_id, order_id)      REFERENCES app.orders (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, quote_line_id) REFERENCES app.quote_lines (tenant_id, id),
  FOREIGN KEY (tenant_id, item_id)       REFERENCES app.items (tenant_id, id)
);

-- ---------------------------------------------------------------------
-- 請求
-- ---------------------------------------------------------------------
CREATE TABLE app.invoices (
  id                          uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                   uuid          NOT NULL REFERENCES app.tenants (id),
  invoice_number              text,          -- 発行時に採番。訂正版は同じ番号で version を上げる
  version                     smallint      NOT NULL DEFAULT 1 CHECK (version >= 1),
  previous_version_id         uuid,          -- 訂正前の版
  superseded_at               timestamptz,   -- 次の版が発行された日時(NULL = 最新版)
  customer_id                 uuid          NOT NULL,
  customer_name               text          NOT NULL,
  customer_honorific          text          NOT NULL,
  customer_address            text,
  issuer_name                 text,          -- 発行時点の自社名のコピー
  issuer_registration_number  text,          -- 発行時点の登録番号のコピー。NULL なら適格請求書ではない
  title                       text,
  issue_date                  date,          -- 請求日
  due_date                    date,          -- 支払期限
  status                      text          NOT NULL DEFAULT 'draft'
                                            CHECK (status IN ('draft', 'issued', 'paid', 'void')),
  subtotal_amount             numeric(15,0) NOT NULL DEFAULT 0,
  tax_amount                  numeric(15,0) NOT NULL DEFAULT 0,
  total_amount                numeric(15,0) NOT NULL DEFAULT 0,
  remarks                     text,
  issued_at                   timestamptz,
  voided_at                   timestamptz,
  created_by                  uuid,
  created_at                  timestamptz   NOT NULL DEFAULT now(),
  updated_at                  timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, invoice_number, version),
  FOREIGN KEY (tenant_id, customer_id)         REFERENCES app.customers (tenant_id, id),
  FOREIGN KEY (tenant_id, previous_version_id) REFERENCES app.invoices (tenant_id, id),
  CHECK (status = 'draft'
         OR (invoice_number IS NOT NULL AND issue_date IS NOT NULL AND issuer_name IS NOT NULL)),
  CHECK ((version = 1) = (previous_version_id IS NULL))
);

CREATE TABLE app.invoice_lines (
  id                uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid             NOT NULL,
  invoice_id        uuid             NOT NULL,
  line_no           smallint         NOT NULL,
  order_line_id     uuid,             -- 直接作成なら NULL。複数の受注をまとめる月締め請求にも対応
  transaction_date  date             NOT NULL,  -- 取引日(納品日)
  item_id           uuid,
  description       text             NOT NULL,
  quantity          numeric(12,3)    NOT NULL DEFAULT 1,
  unit              text,
  unit_price        numeric(15,2)    NOT NULL,
  amount            numeric(15,0)    NOT NULL,
  tax_category      app.tax_category NOT NULL,
  tax_rate          numeric(5,4)     NOT NULL,
  UNIQUE (tenant_id, id),
  UNIQUE (invoice_id, line_no),
  FOREIGN KEY (tenant_id, invoice_id)    REFERENCES app.invoices (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, order_line_id) REFERENCES app.order_lines (tenant_id, id),
  FOREIGN KEY (tenant_id, item_id)       REFERENCES app.items (tenant_id, id)
);
CREATE INDEX invoice_lines_order_line_idx ON app.invoice_lines (tenant_id, order_line_id);

-- 税率ごとの集計(インボイスの記載事項)。消費税額は税率ごとに1回だけ端数処理した値
CREATE TABLE app.invoice_tax_summaries (
  tenant_id       uuid             NOT NULL,
  invoice_id      uuid             NOT NULL,
  tax_category    app.tax_category NOT NULL,
  tax_rate        numeric(5,4)     NOT NULL,
  taxable_amount  numeric(15,0)    NOT NULL,  -- 税率ごとの対価の合計(税抜)
  tax_amount      numeric(15,0)    NOT NULL,
  PRIMARY KEY (invoice_id, tax_category, tax_rate),
  FOREIGN KEY (tenant_id, invoice_id) REFERENCES app.invoices (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE app.payments (
  id          uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid          NOT NULL,
  invoice_id  uuid          NOT NULL,
  paid_on     date          NOT NULL,
  amount      numeric(15,0) NOT NULL CHECK (amount <> 0),
  memo        text,
  created_by  uuid,
  created_at  timestamptz   NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, invoice_id) REFERENCES app.invoices (tenant_id, id)
);
CREATE INDEX payments_invoice_idx ON app.payments (tenant_id, invoice_id);

-- ---------------------------------------------------------------------
-- 採番
-- ---------------------------------------------------------------------
CREATE TABLE app.number_sequences (
  tenant_id   uuid    NOT NULL REFERENCES app.tenants (id),
  doc_type    text    NOT NULL CHECK (doc_type IN ('quote', 'order', 'invoice')),
  period      text    NOT NULL,  -- '2026'(年ごと)または '202611'(月ごと)
  next_value  integer NOT NULL DEFAULT 1 CHECK (next_value >= 1),
  PRIMARY KEY (tenant_id, doc_type, period)
);

-- 現在の会社の設定に従って次の書類番号を返す(例: INV-2026-0001)
-- 同時に呼ばれても番号が重複しないよう、行ロックつきの更新で採番する
CREATE FUNCTION app.next_document_number(p_doc_type text, p_date date)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
  s         app.tenant_settings;
  v_period  text;
  v_value   integer;
  v_prefix  text;
BEGIN
  SELECT * INTO s FROM app.tenant_settings WHERE tenant_id = app.current_tenant_id();
  IF NOT FOUND THEN
    RAISE EXCEPTION '会社の設定(tenant_settings)が見つかりません';
  END IF;

  v_period := CASE s.numbering_period
                WHEN 'month' THEN to_char(p_date, 'YYYYMM')
                ELSE to_char(p_date, 'YYYY')
              END;

  INSERT INTO app.number_sequences AS ns (tenant_id, doc_type, period, next_value)
  VALUES (s.tenant_id, p_doc_type, v_period, 2)
  ON CONFLICT (tenant_id, doc_type, period)
    DO UPDATE SET next_value = ns.next_value + 1
  RETURNING ns.next_value - 1 INTO v_value;

  v_prefix := CASE p_doc_type
                WHEN 'quote' THEN s.quote_prefix
                WHEN 'order' THEN s.order_prefix
                ELSE s.invoice_prefix
              END;

  RETURN v_prefix || '-' || v_period || '-' || lpad(v_value::text, s.number_digits, '0');
END $$;

-- ---------------------------------------------------------------------
-- 電子帳簿保存法への対応(発行控えと訂正削除の履歴)
-- ---------------------------------------------------------------------
CREATE TABLE app.issued_documents (
  id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid          NOT NULL REFERENCES app.tenants (id),
  doc_type          text          NOT NULL CHECK (doc_type IN ('quote', 'invoice')),
  document_id       uuid          NOT NULL,
  document_number   text          NOT NULL,
  version           smallint      NOT NULL DEFAULT 1,
  storage_path      text          NOT NULL,  -- 控えPDFの保存先
  sha256            text          NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  transaction_date  date          NOT NULL,  -- 検索用の取引年月日(書類の日付)
  total_amount      numeric(15,0) NOT NULL,  -- 検索用の取引金額
  customer_name     text          NOT NULL,  -- 検索用の取引先
  issued_by         uuid,
  issued_at         timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, doc_type, document_id, version)
);
CREATE INDEX issued_documents_date_idx     ON app.issued_documents (tenant_id, transaction_date);
CREATE INDEX issued_documents_amount_idx   ON app.issued_documents (tenant_id, total_amount);
CREATE INDEX issued_documents_customer_idx ON app.issued_documents (tenant_id, customer_name);

CREATE TABLE app.audit_logs (
  id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   uuid        NOT NULL REFERENCES app.tenants (id),
  table_name  text        NOT NULL,
  record_id   uuid        NOT NULL,
  action      text        NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
  old_values  jsonb,
  new_values  jsonb,
  actor_id    uuid,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_logs_record_idx ON app.audit_logs (tenant_id, table_name, record_id);

-- ---------------------------------------------------------------------
-- トリガー関数
-- ---------------------------------------------------------------------

-- 変更履歴を audit_logs に記録する
CREATE FUNCTION app.write_audit_log() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  r jsonb := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
BEGIN
  INSERT INTO app.audit_logs (tenant_id, table_name, record_id, action, old_values, new_values, actor_id)
  VALUES (
    (r->>'tenant_id')::uuid,
    TG_TABLE_NAME,
    coalesce(r->>'id', r->>'tenant_id')::uuid,
    lower(TG_OP),
    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END,
    app.current_user_id()
  );
  RETURN NULL;  -- AFTER トリガーなので戻り値は使われない
END $$;

-- 追記専用のテーブルで更新・削除を拒否する
CREATE FUNCTION app.reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% は追記専用のため変更・削除できません', TG_TABLE_NAME;
END $$;

-- 作成中以外の書類は削除させない(取消や訂正版で対応する)
CREATE FUNCTION app.guard_document_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION '作成中以外の書類は削除できません(状態: %)', OLD.status;
  END IF;
  RETURN OLD;
END $$;

-- 見積: 発行後は内容を固定し、状態は 発行済→受注済/失注 だけ認める
CREATE FUNCTION app.guard_quote_update() RETURNS trigger
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
     AND NOT (OLD.status = 'issued' AND NEW.status IN ('ordered', 'lost')) THEN
    RAISE EXCEPTION '見積の状態を % から % には変更できません', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END $$;

-- 請求書: 発行後は内容を固定し、状態は 発行済→入金済/取消、入金済→発行済(入金の取り消し)だけ認める
CREATE FUNCTION app.guard_invoice_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'draft' THEN
    IF NEW.status NOT IN ('draft', 'issued') THEN
      RAISE EXCEPTION '作成中の請求書は発行前に % にできません', NEW.status;
    END IF;
    RETURN NEW;
  END IF;

  IF (to_jsonb(NEW) - ARRAY['status', 'superseded_at', 'voided_at', 'updated_at'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'superseded_at', 'voided_at', 'updated_at']) THEN
    RAISE EXCEPTION '発行済みの請求書は変更できません。訂正版を作成してください';
  END IF;

  IF OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
    RAISE EXCEPTION '訂正済みの印は変更できません';
  END IF;

  IF NEW.status <> OLD.status
     AND NOT ((OLD.status = 'issued' AND NEW.status IN ('paid', 'void'))
              OR (OLD.status = 'paid' AND NEW.status = 'issued')) THEN
    RAISE EXCEPTION '請求書の状態を % から % には変更できません', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END $$;

-- 明細・税率別集計: 親の書類が作成中のときだけ追加・変更・削除を認める
CREATE FUNCTION app.guard_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  r         jsonb := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
  v_status  text;
BEGIN
  IF TG_TABLE_NAME = 'quote_lines' THEN
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

-- ---------------------------------------------------------------------
-- トリガー
-- ---------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  -- updated_at の自動更新
  FOREACH t IN ARRAY ARRAY['tenants', 'tenant_settings', 'company_profiles', 'customers',
                           'items', 'quotes', 'orders', 'invoices']
  LOOP
    EXECUTE format('CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.%I
                    FOR EACH ROW EXECUTE FUNCTION app.set_updated_at()', t);
  END LOOP;

  -- 変更履歴
  FOREACH t IN ARRAY ARRAY['company_profiles', 'quotes', 'quote_lines', 'orders', 'order_lines',
                           'invoices', 'invoice_lines', 'payments']
  LOOP
    EXECUTE format('CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.%I
                    FOR EACH ROW EXECUTE FUNCTION app.write_audit_log()', t);
  END LOOP;

  -- 追記専用
  FOREACH t IN ARRAY ARRAY['issued_documents', 'audit_logs']
  LOOP
    EXECUTE format('CREATE TRIGGER reject_change BEFORE UPDATE OR DELETE ON app.%I
                    FOR EACH ROW EXECUTE FUNCTION app.reject_change()', t);
  END LOOP;

  -- 明細の保護
  FOREACH t IN ARRAY ARRAY['quote_lines', 'invoice_lines', 'invoice_tax_summaries']
  LOOP
    EXECUTE format('CREATE TRIGGER guard_lines BEFORE INSERT OR UPDATE OR DELETE ON app.%I
                    FOR EACH ROW EXECUTE FUNCTION app.guard_lines()', t);
  END LOOP;
END $$;

CREATE TRIGGER guard_quote_update   BEFORE UPDATE ON app.quotes   FOR EACH ROW EXECUTE FUNCTION app.guard_quote_update();
CREATE TRIGGER guard_quote_delete   BEFORE DELETE ON app.quotes   FOR EACH ROW EXECUTE FUNCTION app.guard_document_delete();
CREATE TRIGGER guard_invoice_update BEFORE UPDATE ON app.invoices FOR EACH ROW EXECUTE FUNCTION app.guard_invoice_update();
CREATE TRIGGER guard_invoice_delete BEFORE DELETE ON app.invoices FOR EACH ROW EXECUTE FUNCTION app.guard_document_delete();

-- ---------------------------------------------------------------------
-- RLS(行レベルセキュリティ)
-- ---------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tenant_settings', 'company_profiles', 'customers', 'items',
                           'quotes', 'quote_lines', 'orders', 'order_lines',
                           'invoices', 'invoice_lines', 'invoice_tax_summaries', 'payments',
                           'number_sequences', 'issued_documents', 'audit_logs']
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I
                    USING (tenant_id = app.current_tenant_id())
                    WITH CHECK (tenant_id = app.current_tenant_id())', t);
  END LOOP;
END $$;

-- 所属の一覧はログイン直後(会社を選ぶ前)にも自分の分だけ見えるようにする
ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY;
CREATE POLICY membership_visibility ON app.memberships
  USING (tenant_id = app.current_tenant_id() OR user_id = app.current_user_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

ALTER TABLE app.tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_visibility ON app.tenants
  USING (id = app.current_tenant_id()
         OR id IN (SELECT tenant_id FROM app.memberships WHERE user_id = app.current_user_id()));

-- ---------------------------------------------------------------------
-- 権限
--   API はテーブルの所有者ではない app_user ロールで接続する(所有者は RLS を素通りするため)。
--   ログイン用ユーザーは環境ごとに作り、次のように app_user を付与する。
--     CREATE ROLE api_login LOGIN PASSWORD '...';
--     GRANT app_user TO api_login;
-- ---------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user NOLOGIN;
  END IF;
END $$;

GRANT USAGE ON SCHEMA app TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_user;
REVOKE INSERT, UPDATE, DELETE ON app.tax_categories FROM app_user;
REVOKE UPDATE, DELETE ON app.issued_documents, app.audit_logs FROM app_user;
REVOKE INSERT, UPDATE, DELETE ON app.tenants FROM app_user;  -- 会社の登録は運用側で行う
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO app_user;

COMMIT;
