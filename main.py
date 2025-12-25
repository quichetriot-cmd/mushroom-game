from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import os
import secrets
from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel

from scraper import run_incremental_scrape

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vintage.db")
# Handle Railway's postgres:// vs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Models
class Item(Base):
    __tablename__ = "items"
    
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(500), unique=True, index=True)
    title = Column(String(500), index=True)
    price_yen = Column(Integer)
    price_usd = Column(Integer, index=True)
    description = Column(Text)
    images = Column(Text)  # JSON string of image URLs
    sold_date = Column(Date, index=True)
    created_at = Column(Date, default=date.today)


# Pydantic models
class ItemResponse(BaseModel):
    id: int
    url: Optional[str] = None
    title: str
    price_yen: int
    price_usd: int
    description: str
    images: List[str]
    sold_date: str
    
    class Config:
        from_attributes = True


class StatsResponse(BaseModel):
    total_items: int
    newest_date: str
    oldest_date: str
    last_scrape: str


# Create tables
Base.metadata.create_all(bind=engine)


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Auth
security = HTTPBasic()
SCRAPE_USER = os.getenv("SCRAPE_USER", "admin")
SCRAPE_PASS = os.getenv("SCRAPE_PASS", "changeme")

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, SCRAPE_USER)
    correct_pass = secrets.compare_digest(credentials.password, SCRAPE_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username


# Scheduler for daily scraping
scheduler = BackgroundScheduler()
last_scrape_info = {"time": None, "new_items": 0}
scrape_lock = False


def scheduled_scrape():
    global last_scrape_info, scrape_lock
    
    if scrape_lock:
        print(f"[{datetime.now()}] Scrape already in progress, skipping...")
        return
    
    scrape_lock = True
    print(f"[{datetime.now()}] Running scheduled scrape...")
    db = SessionLocal()
    try:
        new_count = run_incremental_scrape(db)
        last_scrape_info = {"time": datetime.now().isoformat(), "new_items": new_count}
        print(f"[{datetime.now()}] Scrape complete. Added {new_count} new items.")
    except Exception as e:
        print(f"[{datetime.now()}] Scrape failed: {e}")
    finally:
        db.close()
        scrape_lock = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global last_scrape_info
    last_scrape_info = {"time": datetime.now().isoformat(), "new_items": 0}
    
    # Schedule daily scrape at 6 AM UTC
    scheduler.add_job(scheduled_scrape, 'cron', hour=6, minute=0)
    scheduler.start()
    print("Scheduler started - daily scrape at 6 AM UTC")
    
    yield
    
    # Shutdown
    scheduler.shutdown()


app = FastAPI(title="Vintage Mushroom API", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API Routes
@app.get("/api/items", response_model=List[ItemResponse])
def get_items(
    skip: int = 0,
    limit: int = 100,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    search: Optional[str] = None,
    sort: str = "price_desc",
    db: Session = Depends(get_db)
):
    import json
    
    query = db.query(Item)
    
    # Year filter
    if min_year:
        query = query.filter(Item.sold_date >= date(min_year, 1, 1))
    if max_year:
        query = query.filter(Item.sold_date <= date(max_year, 12, 31))
    
    # Search
    if search:
        query = query.filter(Item.title.ilike(f"%{search}%"))
    
    # Sort
    if sort == "price_desc":
        query = query.order_by(Item.price_usd.desc())
    elif sort == "price_asc":
        query = query.order_by(Item.price_usd.asc())
    elif sort == "date_desc":
        query = query.order_by(Item.sold_date.desc())
    elif sort == "date_asc":
        query = query.order_by(Item.sold_date.asc())
    
    items = query.offset(skip).limit(limit).all()
    
    # Convert to response format
    result = []
    for item in items:
        result.append(ItemResponse(
            id=item.id,
            url=item.url,
            title=item.title,
            price_yen=item.price_yen,
            price_usd=item.price_usd,
            description=item.description or "",
            images=json.loads(item.images) if item.images else [],
            sold_date=item.sold_date.isoformat() if item.sold_date else ""
        ))
    
    return result


@app.get("/api/items/random", response_model=List[ItemResponse])
def get_random_items(
    count: int = 20,
    min_year: Optional[int] = None,
    exclude: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get random items for the game.
    
    Args:
        count: Number of items to return (default 20, max 100)
        min_year: Only include items sold after this year
        exclude: Comma-separated list of item IDs to exclude (for endless mode)
    """
    import json
    from sqlalchemy.sql.expression import func
    
    count = min(count, 100)  # Cap at 100
    
    query = db.query(Item)
    
    if min_year:
        query = query.filter(Item.sold_date >= date(min_year, 1, 1))
    
    # Exclude already-seen items (for endless mode / spaced repetition)
    if exclude:
        try:
            exclude_ids = [int(x.strip()) for x in exclude.split(",") if x.strip()]
            if exclude_ids:
                query = query.filter(~Item.id.in_(exclude_ids))
        except ValueError:
            pass  # Ignore malformed exclude param
    
    items = query.order_by(func.random()).limit(count).all()
    
    result = []
    for item in items:
        result.append(ItemResponse(
            id=item.id,
            url=item.url,
            title=item.title,
            price_yen=item.price_yen,
            price_usd=item.price_usd,
            description=item.description or "",
            images=json.loads(item.images) if item.images else [],
            sold_date=item.sold_date.isoformat() if item.sold_date else ""
        ))
    
    return result


@app.get("/api/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    
    total = db.query(Item).count()
    newest = db.query(func.max(Item.sold_date)).scalar()
    oldest = db.query(func.min(Item.sold_date)).scalar()
    
    last_scrape_str = ""
    if last_scrape_info["time"]:
        last_scrape_str = f"{last_scrape_info['time']} - {last_scrape_info['new_items']} new"
    
    return StatsResponse(
        total_items=total,
        newest_date=newest.isoformat() if newest else "",
        oldest_date=oldest.isoformat() if oldest else "",
        last_scrape=last_scrape_str
    )


@app.post("/api/scrape")
def trigger_scrape(
    username: str = Depends(verify_credentials),
    db: Session = Depends(get_db)
):
    """Manually trigger a scrape (requires auth)"""
    global last_scrape_info, scrape_lock
    
    if scrape_lock:
        raise HTTPException(status_code=409, detail="Scrape already in progress")
    
    scrape_lock = True
    try:
        new_count = run_incremental_scrape(db)
        last_scrape_info = {"time": datetime.now().isoformat(), "new_items": new_count}
        return {"message": f"Scrape complete. Added {new_count} new items."}
    finally:
        scrape_lock = False


@app.get("/api/health")
def health_check():
    return {"status": "ok", "time": datetime.now().isoformat()}


# Serve frontend
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")
