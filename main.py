from fastapi import FastAPI, Depends, HTTPException
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

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vintage.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Database Model
class Item(Base):
    __tablename__ = "items"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), index=True)
    price_yen = Column(Integer)
    price_usd = Column(Integer, index=True)
    description = Column(Text)
    images = Column(Text)
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

def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, os.getenv("AUTH_USER", "admin"))
    correct_pass = secrets.compare_digest(credentials.password, os.getenv("AUTH_PASS", "changeme"))
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# Scheduler
scheduler = BackgroundScheduler()
last_scrape_time = "Never"
last_scrape_result = ""


def scheduled_scrape():
    """Run daily scrape"""
    global last_scrape_time, last_scrape_result
    
    print(f"\n[{datetime.now()}] Starting scheduled scrape...")
    
    db = SessionLocal()
    try:
        from scraper import run_smart_scrape
        new_count = run_smart_scrape(db)
        
        last_scrape_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        last_scrape_result = f"Added {new_count} new items"
        
        print(f"[{datetime.now()}] Scrape complete: {last_scrape_result}")
    except Exception as e:
        last_scrape_result = f"Error: {str(e)}"
        print(f"[{datetime.now()}] Scrape failed: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global last_scrape_time
    
    # Schedule daily scrape at 6 AM UTC
    scheduler.add_job(scheduled_scrape, 'cron', hour=6, minute=0)
    
    # Also run immediately on startup if database is empty
    from scraper import get_item_count
    db = SessionLocal()
    count = get_item_count(db)
    db.close()
    
    if count == 0:
        print("Database empty - starting initial scrape in background...")
        scheduler.add_job(scheduled_scrape, 'date')  # Run once now
    
    scheduler.start()
    print(f"Scheduler started - {count} items in database, scrapes daily at 6 AM UTC")
    
    yield
    
    scheduler.shutdown()


app = FastAPI(title="Vintage Mushroom API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ API ROUTES ============

@app.get("/api/items", response_model=List[ItemResponse])
def get_items(
    skip: int = 0,
    limit: int = 100,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    search: Optional[str] = None,
    sort: str = "price_desc",
    db: Session = Depends(get_db),
    user: str = Depends(verify_auth)
):
    import json
    
    query = db.query(Item)
    
    if min_year:
        query = query.filter(Item.sold_date >= date(min_year, 1, 1))
    if max_year:
        query = query.filter(Item.sold_date <= date(max_year, 12, 31))
    
    if search:
        query = query.filter(Item.title.ilike(f"%{search}%"))
    
    if sort == "price_desc":
        query = query.order_by(Item.price_usd.desc())
    elif sort == "price_asc":
        query = query.order_by(Item.price_usd.asc())
    elif sort == "date_desc":
        query = query.order_by(Item.sold_date.desc())
    elif sort == "date_asc":
        query = query.order_by(Item.sold_date.asc())
    
    items = query.offset(skip).limit(limit).all()
    
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
    db: Session = Depends(get_db),
    user: str = Depends(verify_auth)
):
    import json
    from sqlalchemy.sql.expression import func
    
    query = db.query(Item)
    
    if min_year:
        query = query.filter(Item.sold_date >= date(min_year, 1, 1))
    
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
def get_stats(db: Session = Depends(get_db), user: str = Depends(verify_auth)):
    from sqlalchemy import func
    
    total = db.query(Item).count()
    newest = db.query(func.max(Item.sold_date)).scalar()
    oldest = db.query(func.min(Item.sold_date)).scalar()
    
    return StatsResponse(
        total_items=total,
        newest_date=newest.isoformat() if newest else "",
        oldest_date=oldest.isoformat() if oldest else "",
        last_scrape=f"{last_scrape_time} - {last_scrape_result}"
    )


@app.post("/api/scrape")
def trigger_scrape(db: Session = Depends(get_db), user: str = Depends(verify_auth)):
    """Manually trigger a scrape"""
    global last_scrape_time, last_scrape_result
    
    try:
        from scraper import run_smart_scrape
        new_count = run_smart_scrape(db)
        
        last_scrape_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        last_scrape_result = f"Added {new_count} new items"
        
        return {"message": last_scrape_result, "new_items": new_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scrape/full")
def trigger_full_scrape(db: Session = Depends(get_db), user: str = Depends(verify_auth)):
    """Trigger a full scrape (use for initial population)"""
    global last_scrape_time, last_scrape_result
    
    try:
        from scraper import run_full_scrape
        new_count = run_full_scrape(db)
        
        last_scrape_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        last_scrape_result = f"Full scrape: Added {new_count} items"
        
        return {"message": last_scrape_result, "new_items": new_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health_check():
    return {"status": "ok", "time": datetime.now().isoformat()}


# Serve frontend (protected)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_frontend(user: str = Depends(verify_auth)):
    return FileResponse("static/index.html")
