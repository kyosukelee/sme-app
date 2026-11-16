-- =====================================================================
-- 初期スキーマの動作確認(テスト用DBで実行し、最後にすべて取り消す)
--   psql -v ON_ERROR_STOP=1 -f 001_smoke_test.sql
-- 成功すると「OK: ...」が順に表示される
-- =====================================================================
BEGIN;

-- 準備: 所有者として2社を登録する(会社の登録は運用側の作業)
INSERT INTO app.tenants (id, name) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', 'A社'),
  ('bbbbbbbb-0000-0000-0000-000000000002', 'B社');
INSERT INTO app.memberships (tenant_id, user_id, role) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', '11111111-0000-0000-0000-000000000001', 'admin'),
  ('bbbbbbbb-0000-0000-0000-000000000002', '11111111-0000-0000-0000-000000000001', 'member');

-- ここから API と同じ権限で動かす
SET LOCAL ROLE app_user;
SET LOCAL app.user_id = '11111111-0000-0000-0000-000000000001';

-- 会社を選ぶ前でも、自分の所属は2社とも見える
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM app.memberships) = 2, '所属が2件見えるはず';
  RAISE NOTICE 'OK: ログイン直後に所属している会社の一覧が見える';
END $$;

-- B社のデータを作る
SET LOCAL app.tenant_id = 'bbbbbbbb-0000-0000-0000-000000000002';
INSERT INTO app.customers (id, tenant_id, name)
VALUES ('cccccccc-0000-0000-0000-00000000000b', 'bbbbbbbb-0000-0000-0000-000000000002', 'B社の取引先');

-- A社に切り替える
SET LOCAL app.tenant_id = 'aaaaaaaa-0000-0000-0000-000000000001';
INSERT INTO app.tenant_settings (tenant_id) VALUES ('aaaaaaaa-0000-0000-0000-000000000001');
INSERT INTO app.company_profiles (tenant_id, name, registration_number)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', '株式会社A', 'T1234567890123');
INSERT INTO app.customers (id, tenant_id, name)
VALUES ('cccccccc-0000-0000-0000-00000000000a', 'aaaaaaaa-0000-0000-0000-000000000001', 'A社の取引先');

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM app.customers) = 1, 'A社からはA社の取引先だけが見えるはず';
  RAISE NOTICE 'OK: 他社の取引先は見えない(RLS)';
END $$;

DO $$ BEGIN
  BEGIN
    INSERT INTO app.customers (tenant_id, name)
    VALUES ('bbbbbbbb-0000-0000-0000-000000000002', 'なりすまし');
    RAISE EXCEPTION 'NG: 他社の tenant_id で登録できてしまった';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'OK: 他社の tenant_id では登録できない(RLS)';
  END;
END $$;

DO $$ BEGIN
  BEGIN
    INSERT INTO app.invoices (tenant_id, customer_id, customer_name, customer_honorific)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'cccccccc-0000-0000-0000-00000000000b', 'B社の取引先', '御中');
    RAISE EXCEPTION 'NG: 他社の取引先を参照できてしまった';
  EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'OK: 他社の取引先は参照できない(複合外部キー)';
  END;
END $$;

-- 採番
DO $$ BEGIN
  ASSERT app.next_document_number('invoice', '2026-11-12') = 'INV-2026-0001', '1件目の番号';
  ASSERT app.next_document_number('invoice', '2026-11-30') = 'INV-2026-0002', '2件目の番号';
  ASSERT app.next_document_number('quote',   '2026-11-30') = 'Q-2026-0001',   '種別ごとに別の連番';
  ASSERT app.next_document_number('invoice', '2027-01-05') = 'INV-2027-0001', '年が変わると1から';
  UPDATE app.tenant_settings SET numbering_period = 'month', number_digits = 3;
  ASSERT app.next_document_number('invoice', '2026-11-12') = 'INV-202611-001', '月ごと・3桁';
  RAISE NOTICE 'OK: 書類番号の採番(年ごと・月ごと・種別ごと)';
END $$;

-- 税率
DO $$ BEGIN
  ASSERT app.tax_rate_on('standard', '2026-11-12') = 0.10, '標準税率';
  ASSERT app.tax_rate_on('reduced',  '2026-11-12') = 0.08, '軽減税率';
  RAISE NOTICE 'OK: 日付時点の税率を引ける';
END $$;

-- 請求書を作成中で作り、明細を入れて発行する
INSERT INTO app.invoices (id, tenant_id, customer_id, customer_name, customer_honorific)
VALUES ('dddddddd-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-000000000001',
        'cccccccc-0000-0000-0000-00000000000a', 'A社の取引先', '御中');
INSERT INTO app.invoice_lines (tenant_id, invoice_id, line_no, transaction_date, description,
                               quantity, unit_price, amount, tax_category, tax_rate)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'dddddddd-0000-0000-0000-000000000001', 1,
        '2026-11-10', '作業費', 1, 10000, 10000, 'standard', 0.10);
INSERT INTO app.invoice_tax_summaries (tenant_id, invoice_id, tax_category, tax_rate, taxable_amount, tax_amount)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'dddddddd-0000-0000-0000-000000000001', 'standard', 0.10, 10000, 1000);
UPDATE app.invoices
SET invoice_number = 'INV-202611-002', issue_date = '2026-11-12', due_date = '2026-12-31',
    issuer_name = '株式会社A', issuer_registration_number = 'T1234567890123',
    subtotal_amount = 10000, tax_amount = 1000, total_amount = 11000,
    status = 'issued', issued_at = now()
WHERE id = 'dddddddd-0000-0000-0000-000000000001';

DO $$ BEGIN
  BEGIN
    UPDATE app.invoices SET total_amount = 99999 WHERE id = 'dddddddd-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'NG: 発行済みの金額を書き換えられた';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'NG:%' THEN RAISE; END IF;
    RAISE NOTICE 'OK: 発行済みの請求書は内容を変更できない';
  END;
END $$;

DO $$ BEGIN
  BEGIN
    INSERT INTO app.invoice_lines (tenant_id, invoice_id, line_no, transaction_date, description,
                                   quantity, unit_price, amount, tax_category, tax_rate)
    VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'dddddddd-0000-0000-0000-000000000001', 2,
            '2026-11-10', '追加', 1, 500, 500, 'standard', 0.10);
    RAISE EXCEPTION 'NG: 発行済みの請求書に明細を追加できた';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'NG:%' THEN RAISE; END IF;
    RAISE NOTICE 'OK: 発行済みの請求書には明細を追加できない';
  END;
END $$;

DO $$ BEGIN
  BEGIN
    DELETE FROM app.invoices WHERE id = 'dddddddd-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'NG: 発行済みの請求書を削除できた';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'NG:%' THEN RAISE; END IF;
    RAISE NOTICE 'OK: 発行済みの請求書は削除できない';
  END;
END $$;

-- 状態の遷移: 発行済→入金済→発行済(入金取消)は可、入金済→取消は不可
UPDATE app.invoices SET status = 'paid'   WHERE id = 'dddddddd-0000-0000-0000-000000000001';
UPDATE app.invoices SET status = 'issued' WHERE id = 'dddddddd-0000-0000-0000-000000000001';
UPDATE app.invoices SET status = 'paid'   WHERE id = 'dddddddd-0000-0000-0000-000000000001';
DO $$ BEGIN
  BEGIN
    UPDATE app.invoices SET status = 'void' WHERE id = 'dddddddd-0000-0000-0000-000000000001';
    RAISE EXCEPTION 'NG: 入金済みから取消にできた';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'NG:%' THEN RAISE; END IF;
    RAISE NOTICE 'OK: 状態の遷移は決められた順にしか進まない';
  END;
END $$;

-- 訂正版: 同じ番号で第2版を作り、旧版に訂正済みの印を付ける
INSERT INTO app.invoices (id, tenant_id, invoice_number, version, previous_version_id,
                          customer_id, customer_name, customer_honorific)
VALUES ('dddddddd-0000-0000-0000-000000000002', 'aaaaaaaa-0000-0000-0000-000000000001',
        'INV-202611-002', 2, 'dddddddd-0000-0000-0000-000000000001',
        'cccccccc-0000-0000-0000-00000000000a', 'A社の取引先', '御中');
UPDATE app.invoices SET superseded_at = now() WHERE id = 'dddddddd-0000-0000-0000-000000000001';
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM app.invoices WHERE invoice_number = 'INV-202611-002') = 2, '旧版と第2版が両方残る';
  RAISE NOTICE 'OK: 訂正版を作っても旧版は残る';
END $$;

-- 作成中の請求書は明細ごと削除できる
DELETE FROM app.invoices WHERE id = 'dddddddd-0000-0000-0000-000000000002';
DO $$ BEGIN
  RAISE NOTICE 'OK: 作成中の請求書は削除できる';
END $$;

-- 発行控えと変更履歴
INSERT INTO app.issued_documents (tenant_id, doc_type, document_id, document_number, storage_path,
                                  sha256, transaction_date, total_amount, customer_name)
VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'invoice', 'dddddddd-0000-0000-0000-000000000001',
        'INV-202611-002', 'tenants/a/invoices/INV-202611-002_v1.pdf', repeat('a', 64),
        '2026-11-12', 11000, 'A社の取引先');

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM app.audit_logs
          WHERE table_name = 'invoices' AND record_id = 'dddddddd-0000-0000-0000-000000000001') >= 5,
         '請求書の変更履歴が残っているはず';
  ASSERT (SELECT actor_id FROM app.audit_logs ORDER BY id DESC LIMIT 1)
         = '11111111-0000-0000-0000-000000000001', '操作者が記録されるはず';
  RAISE NOTICE 'OK: 変更履歴に操作者つきで記録される';
END $$;

DO $$ BEGIN
  BEGIN
    DELETE FROM app.audit_logs;
    RAISE EXCEPTION 'NG: 変更履歴を削除できた';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'OK: 変更履歴は削除できない';
  END;
  BEGIN
    UPDATE app.issued_documents SET total_amount = 0;
    RAISE EXCEPTION 'NG: 発行控えを書き換えられた';
  EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'OK: 発行控えは書き換えられない';
  END;
END $$;

-- B社に切り替えると、A社の請求書・履歴は見えない
SET LOCAL app.tenant_id = 'bbbbbbbb-0000-0000-0000-000000000002';
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM app.invoices) = 0,         'B社からA社の請求書は見えない';
  ASSERT (SELECT count(*) FROM app.audit_logs
          WHERE tenant_id <> 'bbbbbbbb-0000-0000-0000-000000000002') = 0, 'B社からA社の履歴は見えない';
  ASSERT (SELECT count(*) FROM app.issued_documents) = 0, 'B社からA社の控えは見えない';
  RAISE NOTICE 'OK: 会社を切り替えると他社の書類・履歴・控えは見えない';
END $$;

ROLLBACK;
