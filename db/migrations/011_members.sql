-- 社員(利用者)と担当役割
--   ・ワークフローの承認ルートに使う「上長」「担当役割(経理担当・代表など)」を持たせる
--   ・利用者ID(user_id)はアプリ側の ID。ログイン(Supabase Auth)を入れるときは、
--     認証の ID とこの user_id を対応付ける表を足し、ここで作った ID とデータはそのまま使う
BEGIN;

-- 社員の表示名・部署・上長。所属(memberships)に1対1で付く
CREATE TABLE app.member_profiles (
  tenant_id        uuid        NOT NULL,
  user_id          uuid        NOT NULL,
  display_name     text        NOT NULL CHECK (length(display_name) BETWEEN 1 AND 50),
  email            text        CHECK (email IS NULL OR length(email) <= 254),  -- ログイン導入時の招待に使う
  department       text        CHECK (department IS NULL OR length(department) <= 50),
  manager_user_id  uuid,
  is_active        boolean     NOT NULL DEFAULT true,  -- 退職などで使えなくした社員(履歴の表示のため削除しない)
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id),
  CHECK (manager_user_id IS NULL OR manager_user_id <> user_id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES app.memberships (tenant_id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, manager_user_id) REFERENCES app.member_profiles (tenant_id, user_id)
);
CREATE UNIQUE INDEX member_profiles_email_idx ON app.member_profiles (tenant_id, lower(email)) WHERE email IS NOT NULL;

-- 担当役割(経理担当、代表など)。承認の段で「この役割の誰か1人」を承認者にできる
CREATE TABLE app.approver_groups (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid        NOT NULL REFERENCES app.tenants (id),
  name        text        NOT NULL CHECK (length(name) BETWEEN 1 AND 30),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, name)
);

CREATE TABLE app.approver_group_members (
  tenant_id  uuid NOT NULL,
  group_id   uuid NOT NULL,
  user_id    uuid NOT NULL,
  PRIMARY KEY (tenant_id, group_id, user_id),
  FOREIGN KEY (tenant_id, group_id) REFERENCES app.approver_groups (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, user_id) REFERENCES app.member_profiles (tenant_id, user_id) ON DELETE CASCADE
);

-- すでに所属している利用者にも社員情報を作る(名前はあとで社員の画面から直す)
INSERT INTO app.member_profiles (tenant_id, user_id, display_name)
SELECT tenant_id, user_id, '利用者 ' || left(user_id::text, 8) FROM app.memberships;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['member_profiles', 'approver_groups', 'approver_group_members']
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I
                    USING (tenant_id = app.current_tenant_id())
                    WITH CHECK (tenant_id = app.current_tenant_id())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app.%I TO app_user', t);
  END LOOP;
END $$;

-- ログイン直後(会社を選ぶ前)の会社一覧で、利用を止められた会社を出さないよう、自分の社員情報は読めるようにする。
-- 会社を選んだあとは tenant_isolation だけが効く(ほかの会社の自分の社員情報が混ざらないように)
CREATE POLICY own_profile ON app.member_profiles FOR SELECT
  USING (app.current_tenant_id() IS NULL AND user_id = app.current_user_id());

CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.member_profiles
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.approver_groups
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.member_profiles
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.approver_groups
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

COMMIT;
