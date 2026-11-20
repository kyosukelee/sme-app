-- 入金消込
--   ・銀行の入出金明細(CSV)を取り込み、入金を請求書と突き合わせて記録する
--   ・CSV の列の並びは銀行ごとに違うので、「取込形式」として列の対応を会社ごとに保存する
--   ・取引先が振込手数料を差し引いて振り込んだ差額は、会社設定の上限以内なら「振込手数料」として消し込む
--   ・突き合わせは候補を出して人が確定する(確定すると入金 receipts を作る)
BEGIN;

-- 振込手数料として消し込める差額の上限(0 なら手数料として扱わない)
ALTER TABLE app.tenant_settings
  ADD COLUMN bank_fee_limit numeric(15,0) NOT NULL DEFAULT 880 CHECK (bank_fee_limit BETWEEN 0 AND 10000);

-- 取引先の振込名義(銀行の明細に出るカナの名前)。親会社名で振り込まれるなど、複数持てる
ALTER TABLE app.customers
  ADD COLUMN payer_names text[] NOT NULL DEFAULT '{}';

-- 入金のうち、振込手数料として消し込んだ額。請求書には「入金」と「振込手数料」の2種類で割り当てる
ALTER TABLE app.receipts
  ADD COLUMN fee_amount numeric(15,0) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0);
ALTER TABLE app.payments
  ADD COLUMN kind text NOT NULL DEFAULT 'deposit' CHECK (kind IN ('deposit', 'bank_fee'));

-- 取込形式(列の対応)。列の番号は 0 から数える
CREATE TABLE app.bank_formats (
  id            uuid      PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid      NOT NULL REFERENCES app.tenants (id),
  name          text      NOT NULL CHECK (length(name) BETWEEN 1 AND 50),  -- 例: 〇〇銀行 普通 1234567
  encoding      text      NOT NULL DEFAULT 'auto' CHECK (encoding IN ('auto', 'utf-8', 'shift_jis')),
  skip_rows     smallint  NOT NULL DEFAULT 1 CHECK (skip_rows BETWEEN 0 AND 20),  -- 先頭の見出しなどの行数
  date_col      smallint  NOT NULL CHECK (date_col >= 0),
  amount_col    smallint  NOT NULL CHECK (amount_col >= 0),   -- 入金額の列(出金の行は空か 0 で、取り込まない)
  payer_col     smallint  NOT NULL CHECK (payer_col >= 0),    -- 振込依頼人名・摘要など、名義が入っている列
  memo_col      smallint  CHECK (memo_col >= 0),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, name)
);

-- 取り込み1回分
CREATE TABLE app.bank_imports (
  id                  uuid      PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid      NOT NULL REFERENCES app.tenants (id),
  format_id           uuid,
  filename            text,
  imported_count      integer   NOT NULL DEFAULT 0,
  duplicate_count     integer   NOT NULL DEFAULT 0,
  created_by          uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, format_id) REFERENCES app.bank_formats (tenant_id, id) ON DELETE SET NULL (format_id)
);

-- 取り込んだ入金の明細。確定すると receipt_id に入金が入り、対象外にした明細は ignored になる
CREATE TABLE app.bank_statements (
  id             uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid          NOT NULL REFERENCES app.tenants (id),
  import_id      uuid          NOT NULL,
  txn_date       date          NOT NULL,
  amount         numeric(15,0) NOT NULL CHECK (amount > 0),
  payer_name     text          NOT NULL,           -- 明細に書かれていたまま
  payer_key      text          NOT NULL,           -- 突き合わせ用にそろえた名義(カタカナ・法人略語なし)
  memo           text,
  fingerprint    text          NOT NULL,           -- 同じ明細を二重に取り込まないための値
  receipt_id     uuid,
  ignored        boolean       NOT NULL DEFAULT false,
  ignore_reason  text,
  created_at     timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, fingerprint),
  CHECK (NOT (ignored AND receipt_id IS NOT NULL)),
  FOREIGN KEY (tenant_id, import_id) REFERENCES app.bank_imports (tenant_id, id),
  -- 入金画面で入金を取り消したら、明細は未消込に戻る
  FOREIGN KEY (tenant_id, receipt_id) REFERENCES app.receipts (tenant_id, id) ON DELETE SET NULL (receipt_id)
);
CREATE INDEX bank_statements_open_idx ON app.bank_statements (tenant_id, txn_date)
  WHERE receipt_id IS NULL AND NOT ignored;
CREATE UNIQUE INDEX bank_statements_receipt_idx ON app.bank_statements (tenant_id, receipt_id)
  WHERE receipt_id IS NOT NULL;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['bank_formats', 'bank_imports', 'bank_statements']
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I
                    USING (tenant_id = app.current_tenant_id())
                    WITH CHECK (tenant_id = app.current_tenant_id())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app.%I TO app_user', t);
    EXECUTE format('CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.%I
                    FOR EACH ROW EXECUTE FUNCTION app.write_audit_log()', t);
  END LOOP;
END $$;

CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.bank_formats
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

COMMIT;
