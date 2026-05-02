import json
import os
import threading
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from sqlalchemy import case, create_engine, func
from sqlalchemy.orm import sessionmaker

from apscheduler.schedulers.background import BackgroundScheduler

from models import Base, Item
from scraper import run_smart_scrape, run_sh_scrape, run_acorn_scrape, run_bbj_scrape


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///vintage.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://")


engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine)

Base.metadata.create_all(bind=engine)

app = FastAPI()

scrape_lock = threading.Lock()
YEN_PER_USD = 150
SCRAPING_DISABLED = os.getenv("DISABLE_SCRAPE", "").lower() in {"1", "true", "yes"}
VALID_STORES = {"mushroom", "somethinghappens", "acorn", "berberjin"}


def parse_store_filter(store_value):
    stores = [
        value.strip().lower()
        for value in (store_value or "").split(",")
        if value.strip()
    ]
    if not stores or "all" in stores:
        return []
    return [store for store in stores if store in VALID_STORES]


def effective_price_yen_expression():
    return case(
        (
            (Item.price_yen.is_not(None)) & (Item.price_yen > 0),
            Item.price_yen,
        ),
        else_=func.round(func.coalesce(Item.price_usd, 0) * YEN_PER_USD),
    )


def serialize_item(item: Item) -> dict:
    store = (item.store or "mushroom").strip().lower()
    price_yen = item.price_yen
    if not price_yen and item.price_usd is not None:
        price_yen = round(item.price_usd * YEN_PER_USD)

    sold_date = item.sold_date
    if store == "somethinghappens":
        sold_date = None
    if hasattr(sold_date, "isoformat"):
        sold_date = sold_date.isoformat()

    return {
        "id": item.id,
        "store": store,
        "title": item.title,
        "price_yen": price_yen or 0,
        "price_usd": item.price_usd,
        "description": item.description,
        "images": item.get_images(),
        "sold_date": sold_date,
    }


def run_scrape():
    """Run both Mushroom and Something Happens scrapers."""
    if not scrape_lock.acquire(blocking=False):
        print("Scrape already running")
        return
    try:
        db = SessionLocal()
        run_smart_scrape(db)
        run_sh_scrape(db)
        run_acorn_scrape(db)
        run_bbj_scrape(db)
        db.close()
    except Exception as e:
        print(f"Scrape failed: {e}")
    finally:
        scrape_lock.release()


# Run every day at 2:49 PM
scheduler = BackgroundScheduler()
if not SCRAPING_DISABLED:
    scheduler.add_job(run_scrape, "cron", hour=14, minute=49)
    scheduler.start()


@app.on_event("startup")
def startup_scrape():
    if SCRAPING_DISABLED:
        return
    thread = threading.Thread(target=run_scrape)
    thread.daemon = True
    thread.start()


@app.get("/api/items")
def get_items(
    search: str = "",
    store: str = Query("all", pattern="^(all|mushroom|somethinghappens|acorn|berberjin)$"),
    sort: str = Query(
        "price_desc",
        pattern="^(price_desc|price_asc|date_desc|date_asc)$"
    ),
    skip: int = 0,
    limit: int = 50
):
    limit = min(limit, 500)
    db = SessionLocal()
    query = db.query(Item)
    effective_price_yen = effective_price_yen_expression()

    if search:
        query = query.filter(Item.title.ilike(f"%{search}%"))

    if store != "all":
        query = query.filter(Item.store == store)

    if sort == "price_desc":
        query = query.order_by(effective_price_yen.desc(), Item.sold_date.desc(), Item.id.desc())
    elif sort == "price_asc":
        query = query.order_by(effective_price_yen.asc(), Item.id.asc())
    elif sort == "date_desc":
        query = query.order_by(Item.sold_date.desc(), Item.id.desc())
    elif sort == "date_asc":
        query = query.order_by(Item.sold_date.asc(), Item.id.asc())

    items = query.offset(skip).limit(limit).all()
    results = [serialize_item(item) for item in items]
    db.close()
    return results


@app.get("/api/items/random")
def get_random_items(
    count: int = 10,
    min_year: Optional[int] = None,
    exclude: str = "",
    store: str = Query("all", pattern="^(all|mushroom|somethinghappens|acorn|berberjin)$"),
    stores: str = "",
):
    db = SessionLocal()
    query = db.query(Item)

    if min_year:
        query = query.filter(Item.sold_date >= datetime(min_year, 1, 1))

    selected_stores = parse_store_filter(stores or store)
    if selected_stores:
        query = query.filter(Item.store.in_(selected_stores))

    exclude_ids = []
    if exclude:
        exclude_ids = [int(value) for value in exclude.split(",") if value.isdigit()]
        if exclude_ids:
            query = query.filter(~Item.id.in_(exclude_ids))

    items = query.order_by(func.random()).limit(count).all()
    results = [serialize_item(item) for item in items]
    db.close()
    return results


@app.get("/api/stats")
def get_stats(
    search: str = "",
    store: str = Query("all", pattern="^(all|mushroom|somethinghappens|acorn|berberjin)$"),
):
    db = SessionLocal()
    query = db.query(Item)

    if search:
        query = query.filter(Item.title.ilike(f"%{search}%"))

    if store != "all":
        query = query.filter(Item.store == store)

    total = query.count()
    db.close()
    return {
        "total_items": total
    }


# Static files LAST — must be after all API routes
app.mount("/", StaticFiles(directory="static", html=True), name="static")
