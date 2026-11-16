"""商品・サービスマスタのAPI。

見積・受注・請求の明細で品名を入力するときの候補になる。選ぶと品名・単位・単価・税区分が入る。
明細には選んだ時点の内容を写すので、マスタの単価を変えても作成済みの書類は変わらない。
使わなくなった商品は「使用中止」にする(候補に出なくなる)。
"""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from psycopg import errors
from pydantic import BaseModel, Field

from app.auth import TenantConn
from app.fields import HalfwidthStr, OptionalHalfwidthStr
from app.schemas import TaxCategory

router = APIRouter(prefix="/api/items", tags=["items"])


class ItemIn(BaseModel):
    code: OptionalHalfwidthStr = Field(default=None, max_length=30)  # 商品コード(任意・会社の中で重複不可)
    name: HalfwidthStr = Field(min_length=1, max_length=200)
    unit: OptionalHalfwidthStr = Field(default=None, max_length=20)
    unit_price: Decimal = Field(max_digits=15, decimal_places=2)
    tax_category: TaxCategory = "standard"
    is_active: bool = True


class ItemOut(BaseModel):
    id: UUID
    code: str | None
    name: str
    unit: str | None
    unit_price: Decimal
    tax_category: TaxCategory
    is_active: bool


COLUMNS = "id, code, name, unit, unit_price, tax_category, is_active"


def _params(body: ItemIn) -> dict:
    data = body.model_dump()
    data["code"] = (data["code"] or "").strip() or None  # 空のコードは「コードなし」として扱う
    data["name"] = data["name"].strip()
    if not data["name"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "品名を入力してください")
    return data


def _write(conn, sql: str, params: dict):
    try:
        with conn.transaction():  # 重複エラーでも外側のトランザクションを壊さない
            return conn.execute(sql, params).fetchone()
    except errors.UniqueViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"商品コード「{params['code']}」は、ほかの商品で使われています"
        ) from None


@router.get("", response_model=list[ItemOut])
def list_items(conn: TenantConn, include_inactive: bool = False):
    return conn.execute(
        f"""
        SELECT {COLUMNS} FROM app.items
        WHERE is_active OR %s
        ORDER BY is_active DESC, code NULLS LAST, name
        """,
        (include_inactive,),
    ).fetchall()


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, conn: TenantConn):
    return _write(
        conn,
        f"""
        INSERT INTO app.items (tenant_id, code, name, unit, unit_price, tax_category, is_active)
        VALUES (app.current_tenant_id(), %(code)s, %(name)s, %(unit)s, %(unit_price)s, %(tax_category)s, %(is_active)s)
        RETURNING {COLUMNS}
        """,
        _params(body),
    )


@router.put("/{item_id}", response_model=ItemOut)
def update_item(item_id: UUID, body: ItemIn, conn: TenantConn):
    row = _write(
        conn,
        f"""
        UPDATE app.items
        SET code = %(code)s, name = %(name)s, unit = %(unit)s, unit_price = %(unit_price)s,
            tax_category = %(tax_category)s, is_active = %(is_active)s
        WHERE id = %(id)s
        RETURNING {COLUMNS}
        """,
        {**_params(body), "id": item_id},
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "商品・サービスが見つかりません")
    return row
