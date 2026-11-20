"""取引先マスタのAPI。

締め日・支払月・支払日は取引先ごとに設定でき、空なら自社情報の設定(会社共通)に従う。
取引先を変更しても、発行済みの書類は発行時に写した名前・住所のまま変わらない。
使わなくなった取引先は「使用中止」にする(過去の書類が参照しているので削除はしない)。
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import TenantConn
from app.domain.bank import normalize_payer
from app.fields import HalfwidthStr, OptionalHalfwidthStr

router = APIRouter(prefix="/api/customers", tags=["customers"])


class PaymentTerms(BaseModel):
    closing_day: int = Field(ge=1, le=31)  # 31 = 月末
    payment_month_offset: Literal[0, 1, 2, 3]  # 0=当月、1=翌月…
    payment_day: int = Field(ge=1, le=31)  # 31 = 月末


class CustomerIn(BaseModel):
    name: HalfwidthStr = Field(min_length=1, max_length=200)
    honorific: str = Field(default="御中", max_length=10)
    contact_name: OptionalHalfwidthStr = Field(default=None, max_length=100)
    address: OptionalHalfwidthStr = Field(default=None, max_length=300)
    email: OptionalHalfwidthStr = Field(default=None, max_length=254)
    withholding_default: bool = False  # 請求書を作るとき、源泉徴収をする設定にする(個人事業主が法人などに請求する場合)
    terms: PaymentTerms | None = None  # 取引先ごとの締め日・支払日。空なら会社の設定に従う
    is_active: bool = True
    # 振込名義(銀行の明細に出るカナの名前)。入金消込で、この名義の入金を取引先の候補に出す
    payer_names: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("payer_names")
    @classmethod
    def clean_payer_names(cls, names: list[str]) -> list[str]:
        cleaned, keys = [], set()
        for name in names:
            name = name.strip()
            if not name:
                continue
            if len(name) > 60:
                raise ValueError("振込名義は60文字以内で入力してください")
            key = normalize_payer(name)
            if key and key not in keys:  # 書き方が違うだけの同じ名義は1つにまとめる
                keys.add(key)
                cleaned.append(name)
        return cleaned

    @field_validator("honorific")
    @classmethod
    def honorific_is_known(cls, v: str) -> str:
        if v not in ("御中", "様"):
            raise ValueError("敬称は「御中」か「様」を選んでください")
        return v


class CustomerOut(BaseModel):
    id: UUID
    name: str
    honorific: str
    contact_name: str | None
    address: str | None
    email: str | None
    withholding_default: bool
    terms: PaymentTerms | None
    is_active: bool
    payer_names: list[str]


SELECT = """
    SELECT id, name, honorific, contact_name, address, email, withholding_default, is_active, payer_names,
           closing_day, payment_month_offset, payment_day
    FROM app.customers
"""


def _out(row: dict) -> dict:
    terms = None
    if row["closing_day"] is not None:
        terms = {
            "closing_day": row["closing_day"],
            "payment_month_offset": row["payment_month_offset"],
            "payment_day": row["payment_day"],
        }
    return {**row, "terms": terms}


def _params(body: CustomerIn) -> dict:
    t = body.terms
    return {
        **body.model_dump(exclude={"terms"}),
        "closing_day": t.closing_day if t else None,
        "payment_month_offset": t.payment_month_offset if t else None,
        "payment_day": t.payment_day if t else None,
    }


def _get(conn, customer_id: UUID) -> dict:
    row = conn.execute(f"{SELECT} WHERE id = %s", (customer_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "取引先が見つかりません")
    return _out(row)


@router.get("", response_model=list[CustomerOut])
def list_customers(conn: TenantConn, include_inactive: bool = False):
    # WHERE で会社を絞らなくても、RLS によって選んだ会社の取引先だけが返る
    rows = conn.execute(
        f"{SELECT} WHERE is_active OR %s ORDER BY is_active DESC, name", (include_inactive,)
    ).fetchall()
    return [_out(r) for r in rows]


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: UUID, conn: TenantConn):
    return _get(conn, customer_id)


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(body: CustomerIn, conn: TenantConn):
    row = conn.execute(
        """
        INSERT INTO app.customers
          (tenant_id, name, honorific, contact_name, address, email, withholding_default, is_active, payer_names,
           closing_day, payment_month_offset, payment_day)
        VALUES (app.current_tenant_id(), %(name)s, %(honorific)s, %(contact_name)s, %(address)s, %(email)s,
                %(withholding_default)s, %(is_active)s, %(payer_names)s,
                %(closing_day)s, %(payment_month_offset)s, %(payment_day)s)
        RETURNING id
        """,
        _params(body),
    ).fetchone()
    return _get(conn, row["id"])


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: UUID, body: CustomerIn, conn: TenantConn):
    """取引先を変更する。発行済みの書類は発行時の名前・住所のまま変わらない。

    締め日・支払日の変更は、これから作る請求書の支払期限と締め請求の対象期間から使われる。
    """
    updated = conn.execute(
        """
        UPDATE app.customers
        SET name = %(name)s, honorific = %(honorific)s, contact_name = %(contact_name)s, address = %(address)s,
            email = %(email)s, withholding_default = %(withholding_default)s, is_active = %(is_active)s,
            payer_names = %(payer_names)s,
            closing_day = %(closing_day)s, payment_month_offset = %(payment_month_offset)s,
            payment_day = %(payment_day)s
        WHERE id = %(id)s
        RETURNING id
        """,
        {**_params(body), "id": customer_id},
    ).fetchone()
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "取引先が見つかりません")
    return _get(conn, customer_id)
