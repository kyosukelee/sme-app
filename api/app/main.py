"""API の入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import errors
from app.config import settings
from app.db import pool
from app.routers import archive, bank, company, customers, export, invoices, items, journal, me, members, orders, pricing, quotes, receipts


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # DB の起動を待たずに API を立ち上げ、つながるまで裏で再試行させる
    pool.open(wait=False)
    yield
    pool.close()


app = FastAPI(title="見積・受注・請求 API", lifespan=lifespan)
errors.register(app)
app.include_router(me.router)
app.include_router(customers.router)
app.include_router(members.router)
app.include_router(items.router)
app.include_router(pricing.router)
app.include_router(quotes.router)
app.include_router(orders.router)
app.include_router(invoices.router)
app.include_router(receipts.router)
app.include_router(bank.router)
app.include_router(journal.router)
app.include_router(company.router)
app.include_router(archive.router)
app.include_router(export.router)


@app.get("/api/health")
def health():
    with pool.connection(timeout=5) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


# 本番イメージではビルド済みの画面を同じ API から配信する(API のルートより後に登録する)
if settings.static_dir and settings.static_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
