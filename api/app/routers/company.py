"""自社情報(書類に印字する内容)と、請求の初期設定(締め日・支払日)。変更は管理者だけ。"""

import re
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator, model_validator

from app.auth import AdminConn, TenantConn
from app.domain.text import to_halfwidth
from app.fields import HalfwidthStr, OptionalHalfwidthStr

router = APIRouter(prefix="/api/company", tags=["company"])

REGISTRATION_NUMBER = re.compile(r"^T\d{13}$")


class CompanyProfile(BaseModel):
    business_type: Literal["corporation", "sole_proprietor"] = "corporation"
    name: HalfwidthStr = Field(min_length=1, max_length=200)  # 会社名、または屋号(屋号がなければ氏名)
    representative_name: OptionalHalfwidthStr = Field(default=None, max_length=100)  # 個人事業主は氏名、法人は代表者
    tax_exempt: bool = False  # 免税事業者(消費税を表示せず、金額をそのまま請求する)
    postal_code: OptionalHalfwidthStr = Field(default=None, max_length=10)
    address: OptionalHalfwidthStr = Field(default=None, max_length=300)
    phone: OptionalHalfwidthStr = Field(default=None, max_length=30)
    registration_number: str | None = Field(default=None, description="適格請求書発行事業者の登録番号(T+13桁)。空欄なら免税事業者など")
    bank_account: str | None = Field(default=None, max_length=500)

    @field_validator("registration_number", mode="before")
    @classmethod
    def normalize_registration_number(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        text = to_halfwidth(str(value)).replace("-", "").replace(" ", "").upper()
        if not REGISTRATION_NUMBER.match(text):
            raise ValueError("登録番号は「T」と13桁の数字で入力してください(例: T1234567890123)")
        return text

    @model_validator(mode="after")
    def exempt_needs_no_registration(self) -> "CompanyProfile":
        if self.tax_exempt and self.registration_number:
            raise ValueError(
                "登録番号がある場合は免税事業者にできません(適格請求書発行事業者は課税事業者です)。"
                "免税事業者にするときは登録番号を空欄にしてください"
            )
        return self

    @field_validator("bank_account", mode="before")
    @classmethod
    def normalize_bank_account(cls, value: str | None) -> str | None:
        return to_halfwidth(value).strip() or None if value else None


class PaymentTerms(BaseModel):
    closing_day: int = Field(ge=1, le=31)              # 31 = 月末
    payment_month_offset: Literal[0, 1, 2, 3]          # 0=当月、1=翌月…
    payment_day: int = Field(ge=1, le=31)              # 31 = 月末


class CompanyIn(BaseModel):
    profile: CompanyProfile
    terms: PaymentTerms


class CompanyOut(BaseModel):
    profile: CompanyProfile | None
    terms: PaymentTerms
    can_edit: bool


def _read(conn) -> dict:
    profile = conn.execute(
        """
        SELECT business_type, name, representative_name, tax_exempt,
               postal_code, address, phone, registration_number, bank_account
        FROM app.company_profiles
        """
    ).fetchone()
    terms = conn.execute(
        "SELECT closing_day, payment_month_offset, payment_day FROM app.tenant_settings"
    ).fetchone()
    role = conn.execute(
        "SELECT role FROM app.memberships WHERE tenant_id = app.current_tenant_id() AND user_id = app.current_user_id()"
    ).fetchone()
    return {"profile": profile, "terms": terms, "can_edit": role is not None and role["role"] == "admin"}


@router.get("", response_model=CompanyOut)
def get_company(conn: TenantConn):
    return _read(conn)


@router.put("", response_model=CompanyOut)
def update_company(body: CompanyIn, conn: AdminConn):
    p = body.profile
    conn.execute(
        """
        INSERT INTO app.company_profiles
          (tenant_id, business_type, name, representative_name, tax_exempt,
           postal_code, address, phone, registration_number, bank_account)
        VALUES (app.current_tenant_id(), %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET business_type = EXCLUDED.business_type, name = EXCLUDED.name,
            representative_name = EXCLUDED.representative_name, tax_exempt = EXCLUDED.tax_exempt,
            postal_code = EXCLUDED.postal_code, address = EXCLUDED.address,
            phone = EXCLUDED.phone, registration_number = EXCLUDED.registration_number,
            bank_account = EXCLUDED.bank_account
        """,
        (p.business_type, p.name, p.representative_name, p.tax_exempt,
         p.postal_code, p.address, p.phone, p.registration_number, p.bank_account),
    )
    t = body.terms
    conn.execute(
        "UPDATE app.tenant_settings SET closing_day = %s, payment_month_offset = %s, payment_day = %s",
        (t.closing_day, t.payment_month_offset, t.payment_day),
    )
    return _read(conn)
