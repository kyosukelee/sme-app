-- ワークフロー(申請・承認)
--   ・申請種別ごとに入力項目(定義データ)と承認の段(最大3段、金額条件つき)を持つ
--   ・申請した時点で承認ルートを申請側(request_steps)へコピーする。途中で設定を変えても進行中の申請は変わらない
--   ・状態の変更は API が行い、許されない変更はトリガーで拒否する(見積・請求書と同じ方式)
--   ・申請を見られるのは、申請者・承認ルートに入っている社員・管理者だけ(API の確認と RLS の二重で守る)
BEGIN;

-- ---------------------------------------------------------------------
-- 採番に申請を追加する(例: R-2026-0001)
-- ---------------------------------------------------------------------
ALTER TABLE app.tenant_settings ADD COLUMN request_prefix text NOT NULL DEFAULT 'R';

ALTER TABLE app.number_sequences DROP CONSTRAINT number_sequences_doc_type_check;
ALTER TABLE app.number_sequences
  ADD CONSTRAINT number_sequences_doc_type_check CHECK (doc_type IN ('quote', 'order', 'invoice', 'request'));

CREATE OR REPLACE FUNCTION app.next_document_number(p_doc_type text, p_date date)
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
                WHEN 'request' THEN s.request_prefix
                ELSE s.invoice_prefix
              END;

  RETURN v_prefix || '-' || v_period || '-' || lpad(v_value::text, s.number_digits, '0');
END $$;

-- ---------------------------------------------------------------------
-- 申請種別と承認の段(会社ごとの設定)
-- ---------------------------------------------------------------------
CREATE TABLE app.request_types (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid        NOT NULL REFERENCES app.tenants (id),
  code         text        NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{0,29}$'),  -- expense / purchase / leave / general など
  name         text        NOT NULL CHECK (length(name) BETWEEN 1 AND 30),
  description  text        CHECK (description IS NULL OR length(description) <= 200),
  form_schema  jsonb       NOT NULL DEFAULT '[]',   -- 入力項目の定義 [{"key", "label", "type", "required", ...}]
  amount_mode  text        NOT NULL DEFAULT 'none' CHECK (amount_mode IN ('required', 'optional', 'none')),
  amount_label text        NOT NULL DEFAULT '金額' CHECK (length(amount_label) BETWEEN 1 AND 20),
  sort_order   smallint    NOT NULL DEFAULT 0,
  is_active    boolean     NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, code),
  UNIQUE (tenant_id, name)
);

-- 承認の段。min_amount がある段は、申請の金額がその額以上のときだけ使う
CREATE TABLE app.route_steps (
  id                 uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid          NOT NULL REFERENCES app.tenants (id),
  request_type_id    uuid          NOT NULL,
  step_no            smallint      NOT NULL CHECK (step_no BETWEEN 1 AND 3),
  approver_kind      text          NOT NULL CHECK (approver_kind IN ('manager', 'group', 'user')),
  approver_group_id  uuid,
  approver_user_id   uuid,
  min_amount         numeric(15,0) CHECK (min_amount IS NULL OR min_amount > 0),
  UNIQUE (tenant_id, request_type_id, step_no),
  CHECK (
    (approver_kind = 'manager' AND approver_group_id IS NULL AND approver_user_id IS NULL) OR
    (approver_kind = 'group'   AND approver_group_id IS NOT NULL AND approver_user_id IS NULL) OR
    (approver_kind = 'user'    AND approver_user_id IS NOT NULL AND approver_group_id IS NULL)
  ),
  FOREIGN KEY (tenant_id, request_type_id) REFERENCES app.request_types (tenant_id, id) ON DELETE CASCADE,
  -- 承認の段で使っている担当役割・社員は消せない(先に段を直す)
  FOREIGN KEY (tenant_id, approver_group_id) REFERENCES app.approver_groups (tenant_id, id),
  FOREIGN KEY (tenant_id, approver_user_id) REFERENCES app.member_profiles (tenant_id, user_id)
);

-- ---------------------------------------------------------------------
-- 申請
-- ---------------------------------------------------------------------
CREATE TABLE app.requests (
  id               uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid          NOT NULL REFERENCES app.tenants (id),
  request_number   text,         -- 最初に申請したときに振る
  request_type_id  uuid          NOT NULL,
  applicant_id     uuid          NOT NULL,
  title            text          NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
  form_data        jsonb         NOT NULL DEFAULT '{}',
  amount           numeric(15,0) CHECK (amount IS NULL OR amount >= 0),
  status           text          NOT NULL DEFAULT 'draft'
                                 CHECK (status IN ('draft', 'pending', 'returned', 'approved', 'rejected', 'withdrawn')),
  round            smallint      NOT NULL DEFAULT 0,   -- 申請した回数(差戻し後の再申請で1つ増える)
  current_step     smallint,                           -- 承認待ちの段
  source_type      text,                               -- 将来の連携用(見積の値引き承認・工程の承認など)
  source_id        uuid,
  submitted_at     timestamptz,
  decided_at       timestamptz,
  created_at       timestamptz   NOT NULL DEFAULT now(),
  updated_at       timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, request_number),
  CHECK ((status = 'pending') = (current_step IS NOT NULL)),
  FOREIGN KEY (tenant_id, request_type_id) REFERENCES app.request_types (tenant_id, id),
  FOREIGN KEY (tenant_id, applicant_id) REFERENCES app.member_profiles (tenant_id, user_id)
);
CREATE INDEX requests_applicant_idx ON app.requests (tenant_id, applicant_id, status);
CREATE INDEX requests_status_idx ON app.requests (tenant_id, status);

-- 申請時にコピーした承認ルートと、各段の結果。再申請のたびに round を変えて作り直す
CREATE TABLE app.request_steps (
  id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid        NOT NULL,
  request_id          uuid        NOT NULL,
  round               smallint    NOT NULL CHECK (round >= 1),
  step_no             smallint    NOT NULL CHECK (step_no BETWEEN 1 AND 3),
  approver_kind       text        NOT NULL CHECK (approver_kind IN ('manager', 'group', 'user')),
  label               text        NOT NULL,             -- 画面に出す段の名前(上長・経理担当・社員名)
  candidate_user_ids  uuid[]      NOT NULL DEFAULT '{}', -- この段を承認できる人(誰か1人が承認すればよい)
  status              text        NOT NULL
                                  CHECK (status IN ('waiting', 'pending', 'approved', 'returned', 'rejected', 'skipped', 'cancelled')),
  skip_reason         text,
  acted_by            uuid,
  acted_at            timestamptz,
  comment             text        CHECK (comment IS NULL OR length(comment) <= 1000),
  UNIQUE (tenant_id, request_id, round, step_no),
  FOREIGN KEY (tenant_id, request_id) REFERENCES app.requests (tenant_id, id) ON DELETE CASCADE
);

-- 申請・承認・差戻しなどの履歴(追記のみ)
CREATE TABLE app.request_events (
  id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   uuid        NOT NULL,
  request_id  uuid        NOT NULL,
  round       smallint,
  step_no     smallint,
  action      text        NOT NULL
              CHECK (action IN ('submit', 'resubmit', 'approve', 'return', 'reject', 'withdraw', 'comment', 'skip', 'complete')),
  actor_id    uuid,
  comment     text        CHECK (comment IS NULL OR length(comment) <= 1000),
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (tenant_id, request_id) REFERENCES app.requests (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX request_events_request_idx ON app.request_events (tenant_id, request_id, id);

-- 添付ファイル(領収書など)。ファイルは控えPDFと同じ保存先の仕組みに置く
CREATE TABLE app.request_attachments (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid        NOT NULL,
  request_id    uuid        NOT NULL,
  storage_path  text        NOT NULL,
  file_name     text        NOT NULL CHECK (length(file_name) BETWEEN 1 AND 200),
  content_type  text        NOT NULL,
  size_bytes    integer     NOT NULL CHECK (size_bytes BETWEEN 1 AND 10485760),
  sha256        text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  uploaded_by   uuid,
  created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (tenant_id, request_id) REFERENCES app.requests (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX request_attachments_request_idx ON app.request_attachments (tenant_id, request_id);

-- ---------------------------------------------------------------------
-- トリガー
-- ---------------------------------------------------------------------

-- 申請: 本人だけが作り・直せる。承認待ちになったら内容を固定し、完了した申請は一切変えさせない
CREATE FUNCTION app.guard_request() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.applicant_id IS DISTINCT FROM app.current_user_id() AND app.current_user_id() IS NOT NULL THEN
      RAISE EXCEPTION '申請は本人として作成してください';
    END IF;
    IF NEW.status <> 'draft' THEN
      RAISE EXCEPTION '申請は下書きとして作成してください';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION '申請した後は削除できません。取り下げてください';
    END IF;
    RETURN OLD;
  END IF;

  IF OLD.status IN ('approved', 'rejected', 'withdrawn') THEN
    RAISE EXCEPTION '完了した申請は変更できません。新しい申請を作成してください';
  END IF;
  IF NEW.applicant_id <> OLD.applicant_id THEN
    RAISE EXCEPTION '申請者は変更できません';
  END IF;
  IF OLD.request_number IS NOT NULL AND NEW.request_number IS DISTINCT FROM OLD.request_number THEN
    RAISE EXCEPTION '申請番号は変更できません';
  END IF;
  IF (NEW.request_type_id, NEW.title, NEW.form_data, NEW.amount)
     IS DISTINCT FROM (OLD.request_type_id, OLD.title, OLD.form_data, OLD.amount) THEN
    IF OLD.status NOT IN ('draft', 'returned') THEN
      RAISE EXCEPTION '承認待ちの申請は内容を変更できません。取り下げるか、差し戻しを受けてから直してください';
    END IF;
    IF app.current_user_id() IS NOT NULL AND OLD.applicant_id <> app.current_user_id() THEN
      RAISE EXCEPTION '申請の内容を直せるのは申請者だけです';
    END IF;
  END IF;
  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'draft'    AND NEW.status IN ('pending', 'approved')) OR
       (OLD.status = 'returned' AND NEW.status IN ('pending', 'approved', 'withdrawn')) OR
       (OLD.status = 'pending'  AND NEW.status IN ('approved', 'returned', 'rejected', 'withdrawn'))
     ) THEN
    RAISE EXCEPTION '申請の状態を % から % には変更できません', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END $$;

-- 承認の段: ルートは変えさせず、結果を記録できるのはその段の承認者本人だけ。終わった段は変えさせない
CREATE FUNCTION app.guard_request_step() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION '承認の記録は削除できません';
  END IF;
  IF OLD.status IN ('approved', 'returned', 'rejected', 'skipped', 'cancelled') THEN
    RAISE EXCEPTION '終わった承認の段は変更できません';
  END IF;
  IF (to_jsonb(NEW) - ARRAY['status', 'acted_by', 'acted_at', 'comment'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'acted_by', 'acted_at', 'comment']) THEN
    RAISE EXCEPTION '申請した後の承認ルートは変更できません';
  END IF;
  IF NOT ((OLD.status = 'waiting' AND NEW.status IN ('waiting', 'pending', 'cancelled')) OR
          (OLD.status = 'pending' AND NEW.status IN ('pending', 'approved', 'returned', 'rejected', 'cancelled'))) THEN
    RAISE EXCEPTION '承認の段の状態を % から % には変更できません', OLD.status, NEW.status;
  END IF;
  IF NEW.status IN ('approved', 'returned', 'rejected') THEN
    IF NEW.acted_by IS NULL OR NOT (NEW.acted_by = ANY (OLD.candidate_user_ids)) THEN
      RAISE EXCEPTION 'この段を承認できるのは承認者だけです';
    END IF;
    IF app.current_user_id() IS NOT NULL AND NEW.acted_by <> app.current_user_id() THEN
      RAISE EXCEPTION '承認はログインしている本人として行ってください';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER guard_request BEFORE INSERT OR UPDATE OR DELETE ON app.requests
  FOR EACH ROW EXECUTE FUNCTION app.guard_request();
CREATE TRIGGER guard_request_step BEFORE UPDATE OR DELETE ON app.request_steps
  FOR EACH ROW EXECUTE FUNCTION app.guard_request_step();
CREATE TRIGGER reject_change BEFORE UPDATE OR DELETE ON app.request_events
  FOR EACH ROW WHEN (pg_trigger_depth() < 1) EXECUTE FUNCTION app.reject_change();

CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.request_types
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.route_steps
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.requests
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER write_audit_log AFTER INSERT OR UPDATE OR DELETE ON app.request_attachments
  FOR EACH ROW EXECUTE FUNCTION app.write_audit_log();
CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.request_types
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER set_updated_at BEFORE UPDATE ON app.requests
  FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

-- ---------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['request_types', 'route_steps', 'requests', 'request_steps', 'request_events', 'request_attachments']
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON app.%I
                    USING (tenant_id = app.current_tenant_id())
                    WITH CHECK (tenant_id = app.current_tenant_id())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON app.%I TO app_user', t);
  END LOOP;
END $$;
REVOKE UPDATE, DELETE ON app.request_events FROM app_user;

CREATE FUNCTION app.current_user_is_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM app.memberships
                 WHERE tenant_id = app.current_tenant_id() AND user_id = app.current_user_id() AND role = 'admin')
$$;

-- 申請を見てよいか: 申請者本人、承認ルートに入っている社員(過去の回を含む)、管理者。
-- 承認の段は RLS で申請に絞り込まれるので、ここだけは所有者の権限で段を読む(行の絞り込みが循環しないように)
CREATE FUNCTION app.can_view_request(p_request_id uuid, p_applicant_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, app AS $$
  SELECT p_applicant_id = app.current_user_id()
      OR app.current_user_is_admin()
      OR EXISTS (SELECT 1 FROM app.request_steps s
                 WHERE s.tenant_id = app.current_tenant_id() AND s.request_id = p_request_id
                   AND app.current_user_id() = ANY (s.candidate_user_ids))
$$;
REVOKE ALL ON FUNCTION app.can_view_request(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.can_view_request(uuid, uuid), app.current_user_is_admin() TO app_user;

-- 見られない申請は、一覧にも詳細にも出ない(RESTRICTIVE: tenant_isolation と両方を満たす行だけ見える)
CREATE POLICY request_visibility ON app.requests AS RESTRICTIVE FOR SELECT
  USING (app.can_view_request(id, applicant_id));
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['request_steps', 'request_events', 'request_attachments']
  LOOP
    EXECUTE format('CREATE POLICY request_visibility ON app.%I AS RESTRICTIVE FOR SELECT
                    USING (EXISTS (SELECT 1 FROM app.requests r WHERE r.id = request_id))', t);
  END LOOP;
END $$;

COMMIT;
