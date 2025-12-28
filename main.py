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
"""
Optimized trends analysis backend.
Add this to main.py to replace frontend calculation.
"""

from functools import lru_cache
from datetime import datetime, timedelta
import re
import json
from typing import Dict, List, Tuple
from sqlalchemy import func
from collections import defaultdict

# Compile all regexes once at startup
CATEGORIES = {
    # === LEVI'S JEANS ===
    "Levi's S501XX (WW2)": re.compile(r"levi.*s501|s501xx", re.IGNORECASE),
    "Levi's 501XX Buckleback": re.compile(r"levi.*501.*buckleback", re.IGNORECASE),
    "Levi's 501XX": re.compile(r"levi.*501.*xx|levi.*501zxx", re.IGNORECASE),
    "Levi's 501 Big E": re.compile(r"levi.*501.*big\s*e|levi.*501e\b", re.IGNORECASE),
    "Levi's 501 66 Single": re.compile(r"levi.*501.*66", re.IGNORECASE),
    "Levi's 501 (Other)": re.compile(r"levi.*501", re.IGNORECASE),
    "Levi's 505 Big E": re.compile(r"levi.*505.*big\s*e|levi.*505e\b", re.IGNORECASE),
    "Levi's 505 66 Single": re.compile(r"levi.*505.*66|levi.*505ss", re.IGNORECASE),
    "Levi's 606 Big E": re.compile(r"levi.*606", re.IGNORECASE),
    "Levi's 517 Jeans": re.compile(r"levi.*\b517\b(?!xx)", re.IGNORECASE),
    
    # === LEVI'S JACKETS ===
    "Levi's 506XX (1st)": re.compile(r"506xx", re.IGNORECASE),
    "Levi's 507XX (2nd)": re.compile(r"levi.*507.*xx|levi.*507bxx", re.IGNORECASE),
    "Levi's 517XX": re.compile(r"levi.*517xx", re.IGNORECASE),
    "Levi's 557XX": re.compile(r"levi.*557.*xx|levi.*557e\b", re.IGNORECASE),
    "Levi's 70505 (3rd)": re.compile(r"levi.*70505", re.IGNORECASE),
    "Levi's 70506": re.compile(r"levi.*70506", re.IGNORECASE),
    "Levi's Shorthorn": re.compile(r"levi.*shorthorn", re.IGNORECASE),
    
    # === LEE ===
    "Lee 101-J Jacket": re.compile(r"lee.*101-?j\b", re.IGNORECASE),
    "Lee 91-J Chore": re.compile(r"lee.*91-j|lee.*91j", re.IGNORECASE),
    "Lee 101Z": re.compile(r"lee.*101z", re.IGNORECASE),
    "Lee Storm Rider": re.compile(r"lee.*storm\s*rider", re.IGNORECASE),
    "Lee Westerner": re.compile(r"lee.*westerner", re.IGNORECASE),
    
    # === CHAMPION ===
    "Champion Reverse Weave": re.compile(r"reverse\s*weave", re.IGNORECASE),
    "Champion W/F Double Face": re.compile(r"champion.*w\/f|double\s*face", re.IGNORECASE),
    "Champion USMA": re.compile(r"usma|west\s*point", re.IGNORECASE),
    "Champion Football Tee": re.compile(r"champion.*football", re.IGNORECASE),
    
    # === MILITARY ===
    "Navy N-1 Deck": re.compile(r"n-1.*deck|n-1\b", re.IGNORECASE),
    "Navy Dungaree Jacket": re.compile(r"navy.*dungaree.*jacket|usn.*dungaree.*jacket", re.IGNORECASE),
    "USAF MA-1": re.compile(r"ma-1", re.IGNORECASE),
    "USAF A-2": re.compile(r"usaa?f.*a-2|a-2.*flight", re.IGNORECASE),
    "Army M-65": re.compile(r"m-65", re.IGNORECASE),
    
    # === WORKWEAR ===
    "Carhartt": re.compile(r"carhartt", re.IGNORECASE),
    "Big Yank": re.compile(r"big\s*yank", re.IGNORECASE),
    "Pay Day": re.compile(r"pay\s*day", re.IGNORECASE),
    "Hercules": re.compile(r"hercules", re.IGNORECASE),
    
    # === OUTDOOR ===
    "Patagonia": re.compile(r"patagonia", re.IGNORECASE),
    "L.L.Bean": re.compile(r"l\.?l\.?\s*bean", re.IGNORECASE),
    "North Face": re.compile(r"north\s*face", re.IGNORECASE),
    
    # === SOUVENIR ===
    "Vietnam Souvenir": re.compile(r"viet-?nam.*souvenir", re.IGNORECASE),
    "Japan Souvenir": re.compile(r"japan.*souvenir|sukajan", re.IGNORECASE),
    
    # Add more categories as needed...
}

# Cache results for 1 hour
_trends_cache = {"data": None, "timestamp": None}
CACHE_DURATION = 3600  # 1 hour in seconds


def categorize_item(title: str) -> str:
    """Fast category matching with compiled regexes"""
    for category, pattern in CATEGORIES.items():
        if pattern.search(title):
            return category
    return "Other"


def calculate_trends(db: Session) -> Dict:
    """
    Calculate market trends with optimized queries.
    Returns category stats comparing this month vs trailing 6 months.
    """
    global _trends_cache
    
    # Check cache
    now = datetime.now()
    if _trends_cache["data"] and _trends_cache["timestamp"]:
        age = (now - _trends_cache["timestamp"]).total_seconds()
        if age < CACHE_DURATION:
            return _trends_cache["data"]
    
    from main import Item
    
    # Date ranges
    this_month_start = datetime(now.year, now.month, 1).date()
    six_months_ago = (now - timedelta(days=180)).date()
    
    # Fetch items in one query (much faster than multiple queries)
    items = db.query(Item).filter(
        Item.sold_date >= six_months_ago
    ).all()
    
    # Categorize all items at once
    this_month = []
    trailing = []
    
    for item in items:
        category = categorize_item(item.title)
        item_dict = {
            "category": category,
            "price_usd": item.price_usd,
            "sold_date": item.sold_date
        }
        
        if item.sold_date >= this_month_start:
            this_month.append(item_dict)
        else:
            trailing.append(item_dict)
    
    # Calculate stats per category
    category_stats = defaultdict(lambda: {
        "recent_count": 0,
        "recent_total": 0,
        "trailing_count": 0,
        "trailing_total": 0
    })
    
    for item in this_month:
        cat = item["category"]
        category_stats[cat]["recent_count"] += 1
        category_stats[cat]["recent_total"] += item["price_usd"]
    
    for item in trailing:
        cat = item["category"]
        category_stats[cat]["trailing_count"] += 1
        category_stats[cat]["trailing_total"] += item["price_usd"]
    
    # Calculate changes
    results = []
    for category, stats in category_stats.items():
        recent_count = stats["recent_count"]
        trailing_count = stats["trailing_count"]
        trailing_monthly_avg = trailing_count / 6
        
        # Skip categories with insufficient data
        if trailing_count < 3:
            continue
        
        recent_avg_price = stats["recent_total"] / recent_count if recent_count > 0 else 0
        trailing_avg_price = stats["trailing_total"] / trailing_count if trailing_count > 0 else 0
        
        price_change = 0
        if trailing_avg_price > 0 and recent_count > 0:
            price_change = ((recent_avg_price - trailing_avg_price) / trailing_avg_price) * 100
        
        volume_change = 0
        if trailing_monthly_avg > 0:
            volume_change = ((recent_count - trailing_monthly_avg) / trailing_monthly_avg) * 100
        elif recent_count > 0:
            volume_change = 100
        
        results.append({
            "category": category,
            "recent_count": recent_count,
            "recent_avg_price": round(recent_avg_price),
            "trailing_count": trailing_count,
            "trailing_monthly_avg": round(trailing_monthly_avg, 1),
            "trailing_avg_price": round(trailing_avg_price),
            "price_change": round(price_change),
            "volume_change": round(volume_change)
        })
    
    # Sort by price change
    results.sort(key=lambda x: x["price_change"], reverse=True)
    
    # Generate summary
    hot = [r for r in results if r["price_change"] > 20 and r["recent_count"] >= 2]
    cold = [r for r in results if r["price_change"] < -20 and r["recent_count"] >= 2]
    volume_up = [r for r in results if r["volume_change"] > 50 and r["recent_count"] >= 2]
    volume_down = [r for r in results if r["recent_count"] == 0 and r["trailing_monthly_avg"] >= 1]
    
    summary = generate_summary(hot, cold, volume_up, volume_down, len(this_month), len(trailing))
    
    response = {
        "all_categories": results,
        "hot": hot[:5],
        "cold": cold[:5],
        "volume_up": volume_up[:3],
        "volume_down": volume_down[:5],
        "summary": summary,
        "cached_at": now.isoformat()
    }
    
    # Cache result
    _trends_cache["data"] = response
    _trends_cache["timestamp"] = now
    
    return response


def generate_summary(hot, cold, volume_up, volume_down, recent_total, trailing_total):
    """Generate human-readable summary"""
    summary = []
    
    if hot:
        top_hot = ", ".join([f"{r['category']} (+{r['price_change']}%)" for r in hot[:2]])
        summary.append(f"<strong>Prices up:</strong> {top_hot}")
    
    if cold:
        top_cold = ", ".join([f"{r['category']} ({r['price_change']}%)" for r in cold[:2]])
        summary.append(f"<strong>Prices down:</strong> {top_cold}")
    
    if volume_down:
        not_listed = ", ".join([r["category"] for r in volume_down[:3]])
        summary.append(f"<strong>Not listing this month:</strong> {not_listed}")
    
    if volume_up:
        pushing = ", ".join([r["category"] for r in volume_up[:2]])
        summary.append(f"<strong>Pushing more:</strong> {pushing}")
    
    trailing_monthly = round(trailing_total / 6)
    summary.append(f"<br><br><strong>This month:</strong> {recent_total} items (avg {trailing_monthly}/mo)")
    
    if not any([hot, cold, volume_down, volume_up]):
        summary.insert(0, "Market appears stable this month. No major shifts detected.")
    
    return ". ".join(summary) + "."


# Add this endpoint to main.py
@app.get("/api/trends")
def get_trends(db: Session = Depends(get_db)):
    """Get market trends analysis (cached for 1 hour)"""
    return calculate_trends(db)


# Optional: Add endpoint to test single category
@app.get("/api/trends/test")
def test_category(title: str):
    """Test what category a title matches"""
    category = categorize_item(title)
    
    # Find which regex matched
    matched_pattern = None
    for cat, pattern in CATEGORIES.items():
        if cat == category:
            matched_pattern = pattern.pattern
            break
    
    return {
        "title": title,
        "category": category,
        "pattern": matched_pattern
    }


# Optional: Clear cache manually
@app.post("/api/trends/clear-cache")
def clear_trends_cache(username: str = Depends(verify_credentials)):
    """Clear trends cache (requires auth)"""
    global _trends_cache
    _trends_cache = {"data": None, "timestamp": None}
    return {"message": "Cache cleared"}

# Serve frontend
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")
