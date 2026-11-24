-- 操作の記録の強化
--   ・変更履歴(audit_logs)の対象に、会社の設定・利用者の権限・担当役割の社員を加える
--   ・変更履歴に、API のリクエストID を残す(アプリのログと突き合わせるため)
--   ・「見た・持ち出した」記録(activity_logs)を追加する。データ一式の書き出し、仕訳CSV、
--     発行控えのPDF、申請の添付、取り込んだ注文書PDF を開いたときに API が書く
BEGIN;

-- ---------------------------------------------------------------------
-- 変更履歴
-- ---------------------------------------------------------------------
ALTER TABLE app.audit_logs ADD COLUMN request_id text;
CREATE INDEX audit_logs_list_idx ON app.audit_logs (tenant_id, id DESC);

-- id を持たない表(利用者の所属・担当役割の社員)は、利用者ID を記録の対象にする。
-- 会社に1行だけの表(会社の設定など)は、これまでどおり会社ID になる。
-- 何も変わっていない更新(同じ内容で保存し直した、updated_at だけが変わった)は記録しない
CREATE OR REPLACE FUNCTION app.write_audit_log() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  r jsonb := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
BEGIN
  IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) - 'updated_at') = (to_jsonb(NEW) - 'updated_at') THEN
    RETURN NULL;
  END IF;
  INSERT INTO app.audit_logs (tenant_id, table_name, record_id, action, old_values, new_values, actor_id, request_id)
  VALUES (
    (r->>'tenant_id')::uuid,
    TG_TABLE_NAME,
    coalesce(r->>'id', r->>'user_id', r->>'tenant_id')::uuid,
    lower(TG_OP),
    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END,
    app.current_user_id(),
    nullif(current_setting('app.request_id', true), '')
  );
  RETURN NULL;  -- AFTER トリガーなので戻り値は使われない
END $$;

CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.tenant_settings
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.memberships
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.approver_group_members
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();

-- ---------------------------------------------------------------------
-- 見た・持ち出した記録(追記のみ)
-- ---------------------------------------------------------------------
CREATE TABLE app.activity_logs (
  id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id    uuid        NOT NULL REFERENCES app.tenants (id),
  actor_id     uuid,
  action       text        NOT NULL CHECK (action ~ '^[a-z][a-z_]{0,39}$'),  -- export_all / open_document など
  target_type  text,
  target_id    uuid,
  summary      text        NOT NULL CHECK (length(summary) BETWEEN 1 AND 300),  -- 例: 請求書 INV-2026-0002 第1版
  request_id   text,
  created_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX activity_logs_list_idx ON app.activity_logs (tenant_id, id DESC);

ALTER TABLE app.activity_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON app.activity_logs
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
GRANT SELECT, INSERT ON app.activity_logs TO app_user;
CREATE TRIGGER reject_change BEFORE UPDATE OR DELETE ON app.activity_logs
  FOR EACH ROW EXECUTE FUNCTION app.reject_change();

COMMIT;
