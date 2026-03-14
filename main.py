import json
import os
import threading
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from apscheduler.schedulers.background import BackgroundScheduler

from models import Base, Item
from scraper import run_incremental_scrape


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///vintage.db")

# Handle multiple postgres formats safely
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://")


engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine)

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.mount("/", StaticFiles(directory="static", html=True), name="static")


scrape_lock = threading.Lock()


def run_scrape():
    if not scrape_lock.acquire(blocking=False):
        print("Scrape already running")
        return

    try:
        db = SessionLocal()
        run_incremental_scrape(db)
        db.close()
    finally:
        scrape_lock.release()


scheduler = BackgroundScheduler()
scheduler.add_job(run_scrape, "cron", hour=6)
scheduler.start()


@app.on_event("startup")
def startup_scrape():
    run_scrape()


@app.get("/api/items")
def get_items(
    search: str = "",
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

    if search:
        query = query.filter(Item.title.ilike(f"%{search}%"))

    if sort == "price_desc":
        query = query.order_by(Item.price_yen.desc())

    elif sort == "price_asc":
        query = query.order_by(Item.price_yen.asc())

    elif sort == "date_desc":
        query = query.order_by(Item.sold_date.desc())

    elif sort == "date_asc":
        query = query.order_by(Item.sold_date.asc())

    items = query.offset(skip).limit(limit).all()

    results = []

    for item in items:

        images = item.get_images()

        results.append({
            "id": item.id,
            "title": item.title,
            "price_yen": item.price_yen,
            "price_usd": item.price_usd,
            "description": item.description,
            "images": images,
            "sold_date": item.sold_date
        })

    db.close()

    return results


@app.get("/api/items/random")
def get_random_items(count: int = 10):

    db = SessionLocal()

    items = db.query(Item).order_by(func.random()).limit(count).all()

    results = []

    for item in items:

        images = item.get_images()

        results.append({
            "id": item.id,
            "title": item.title,
            "price_yen": item.price_yen,
            "price_usd": item.price_usd,
            "description": item.description,
            "images": images,
            "sold_date": item.sold_date
        })

    db.close()

    return results
