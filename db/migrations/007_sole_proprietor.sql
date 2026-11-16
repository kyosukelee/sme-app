-- =====================================================================
-- 個人事業主への対応
--   ・事業形態(法人/個人事業主)と、屋号と並べて印字する氏名(法人は代表者)
--   ・免税事業者: 消費税を表示せず、金額をそのまま請求する
--   ・源泉徴収: 個人事業主が法人などに請求するとき、源泉徴収税額を差し引いた額を請求する
-- =====================================================================
BEGIN;

ALTER TABLE app.company_profiles
  ADD COLUMN business_type text NOT NULL DEFAULT 'corporation'
    CHECK (business_type IN ('corporation', 'sole_proprietor')),
  ADD COLUMN representative_name text,          -- 個人事業主は氏名、法人は代表者(肩書きを含めてよい)
  ADD COLUMN tax_exempt boolean NOT NULL DEFAULT false,
  -- 適格請求書発行事業者(登録番号あり)は課税事業者なので、免税事業者にはできない
  ADD CONSTRAINT company_profiles_exempt_without_registration
    CHECK (NOT (tax_exempt AND registration_number IS NOT NULL));

-- 取引先ごとの源泉徴収の初期値(請求書を作るときの既定)
ALTER TABLE app.customers
  ADD COLUMN withholding_default boolean NOT NULL DEFAULT false;

ALTER TABLE app.invoices
  ADD COLUMN withholding boolean NOT NULL DEFAULT false,
  ADD COLUMN withholding_amount numeric(15,0) NOT NULL DEFAULT 0 CHECK (withholding_amount >= 0),
  -- 発行時点の発行者情報の写し(登録番号と同じく、あとで自社情報を変えても書類は変わらない)
  ADD COLUMN issuer_representative text,
  ADD COLUMN issuer_tax_exempt boolean NOT NULL DEFAULT false;

-- 受注明細の請求状況のビューは変わらない。入金の判定は API 側で
-- 「合計 − 源泉徴収税額(差引ご請求額)」を基準にする

COMMIT;
