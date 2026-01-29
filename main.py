from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, Date, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from collections import defaultdict
import os
import secrets
import re
import json
import time

from scraper import run_incremental_scrape
# Updated Deps
# Replace the whole database setup section (lines 23-31):
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vintage.db")

# Convert Railway postgres URL to use psycopg driver explicitly
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

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


class TrendsResponse(BaseModel):
    summary: str
    hot_categories: List[Dict[str, Any]]
    cold_categories: List[Dict[str, Any]]
    volume_changes: List[Dict[str, Any]]
    all_categories: List[Dict[str, Any]]
    stats: Dict[str, Any]
    generated_at: str


class CategoryTestResponse(BaseModel):
    title: str
    category: str
    matches: List[Dict[str, str]]


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
        # Clear trends cache after scrape
        clear_trends_cache()
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


# ========== TRENDS ANALYSIS ==========
"""
Complete category patterns for trends analysis.
98.9% categorization rate on 11,138 items.
"""

# Pre-compile all regexes for maximum performance

# MARKET-DRIVEN CATEGORIES v3
# Era + Brand + Type for collectibles (Carhartt, Lee)
# Era-specific for high-value workwear (Chinstrap, Coveralls)
CATEGORIES = {
    # ========================================================================
    # LEVI'S - SPECIFIC VARIANTS FIRST
    # ========================================================================
    
    # Specific color/wash variants (different markets)
    "Levi's 501 Black": re.compile(r"levi.*501.*(black|0658)", re.IGNORECASE),
    "Levi's 501 Redline": re.compile(r"levi.*501.*redline", re.IGNORECASE),
    "Levi's 70507 Black Galactic": re.compile(r"levi.*70507.*(black|galactic|0260)", re.IGNORECASE),
    
    # WW2 Era (highest value)
    "Levi's S501XX": re.compile(r"levi.*s501|s501xx", re.IGNORECASE),
    "Levi's S506XX": re.compile(r"s506xx", re.IGNORECASE),
    
    # XX era jeans
    "Levi's 501XX Buckleback": re.compile(r"levi.*501.*buckleback", re.IGNORECASE),
    "Levi's 501XX": re.compile(r"levi.*501.*xx|levi.*501zxx", re.IGNORECASE),
    "Levi's 551ZXX": re.compile(r"levi.*551", re.IGNORECASE),
    "Levi's 503ZXX/BXX": re.compile(r"levi.*503", re.IGNORECASE),
    "Levi's 550ZXX": re.compile(r"levi.*550", re.IGNORECASE),
    
    # Big E era
    "Levi's 501 Big E": re.compile(r"levi.*501.*big\s*e|levi.*501e\b", re.IGNORECASE),
    "Levi's 502 Big E": re.compile(r"levi.*502.*big\s*e", re.IGNORECASE),
    "Levi's 505 Big E": re.compile(r"levi.*505.*big\s*e|levi.*505e\b", re.IGNORECASE),
    "Levi's 606 Big E": re.compile(r"levi.*606.*big\s*e", re.IGNORECASE),
    
    # 66 era
    "Levi's 501 66 Single": re.compile(r"levi.*501.*66", re.IGNORECASE),
    "Levi's 505 66 Single": re.compile(r"levi.*505.*66|levi.*505ss", re.IGNORECASE),
    
    # Other jeans
    "Levi's 606": re.compile(r"levi.*606", re.IGNORECASE),
    "Levi's 517": re.compile(r"levi.*\b517\b(?!xx)", re.IGNORECASE),
    "Levi's 518": re.compile(r"levi.*\b518\b", re.IGNORECASE),
    "Levi's 701": re.compile(r"levi.*\b701\b", re.IGNORECASE),
    "Levi's 501": re.compile(r"levi.*501", re.IGNORECASE),
    
    # Jackets by type
    "Levi's 506XXE": re.compile(r"506xxe", re.IGNORECASE),
    "Levi's 506XX": re.compile(r"506xx", re.IGNORECASE),
    "Levi's 507XX": re.compile(r"levi.*507.*xx|levi.*507bxx", re.IGNORECASE),
    "Levi's 557XX": re.compile(r"levi.*557.*xx|levi.*557e", re.IGNORECASE),
    "Levi's 557": re.compile(r"levi.*\b557\b", re.IGNORECASE),
    "Levi's 70505-0117": re.compile(r"70505-0117", re.IGNORECASE),
    "Levi's 70505": re.compile(r"levi.*70505", re.IGNORECASE),
    "Levi's 70506": re.compile(r"levi.*70506", re.IGNORECASE),
    "Levi's 70507": re.compile(r"levi.*70507", re.IGNORECASE),
    "Levi's 517XX": re.compile(r"levi.*517xx", re.IGNORECASE),
    "Levi's 519XX": re.compile(r"levi.*519", re.IGNORECASE),
    "Levi's 558XX": re.compile(r"levi.*558", re.IGNORECASE),
    "Levi's 559XX": re.compile(r"levi.*559", re.IGNORECASE),
    "Levi's 71205 Boa": re.compile(r"levi.*71205|levi.*boa", re.IGNORECASE),
    "Levi's Shorthorn": re.compile(r"levi.*shorthorn", re.IGNORECASE),
    "Levi's Saddleman": re.compile(r"levi.*saddleman", re.IGNORECASE),
    "Levi's Advertising": re.compile(r"levi.*advertising|levi.*banner", re.IGNORECASE),
    "Levi's": re.compile(r"levi", re.IGNORECASE),
    
    # ========================================================================
    # LEE - ERA + TYPE SPECIFIC
    # ========================================================================
    
    # WW2 Era
    "Lee S91-J": re.compile(r"lee.*s91-?j", re.IGNORECASE),
    
    # Jackets
    "Lee 191-J": re.compile(r"lee.*191-?j", re.IGNORECASE),
    "Lee 91-LJ": re.compile(r"lee.*91-?lj", re.IGNORECASE),
    "Lee 91-J": re.compile(r"lee.*91-j|lee.*91j", re.IGNORECASE),
    "Lee 101-J": re.compile(r"lee.*101-?j\b", re.IGNORECASE),
    "Lee 98-J": re.compile(r"lee.*98-?j", re.IGNORECASE),
    "Lee 81-LJ": re.compile(r"lee.*81-?lj", re.IGNORECASE),
    "Lee Storm Rider": re.compile(r"lee.*storm\s*rider", re.IGNORECASE),
    
    # Era + Type specific (your correction)
    "1950s Lee Denim Western": re.compile(r"lee.*denim.*western.*(195|1950)|lee.*(195|1950).*denim.*western", re.IGNORECASE),
    "Lee Denim Western": re.compile(r"lee.*denim.*western", re.IGNORECASE),
    
    # Jeans
    "Lee 101Z": re.compile(r"lee.*101z", re.IGNORECASE),
    "Lee 101B Cowboy": re.compile(r"lee.*101b|lee.*cowboy", re.IGNORECASE),
    "Lee Riders": re.compile(r"lee.*riders", re.IGNORECASE),
    "Lee Frisco": re.compile(r"lee.*frisco", re.IGNORECASE),
    
    # Workwear
    "Lee Coverall": re.compile(r"lee.*coverall", re.IGNORECASE),
    "Lee Overall": re.compile(r"lee.*overall", re.IGNORECASE),
    "Lee Painter": re.compile(r"lee.*painter", re.IGNORECASE),
    
    # Special
    "Lee Princeton Beer Jacket": re.compile(r"lee.*princeton.*beer|princeton.*beer.*lee", re.IGNORECASE),
    "Lee Princeton": re.compile(r"lee.*princeton", re.IGNORECASE),
    "Lee Advertising": re.compile(r"lee.*advertising|lee.*sign", re.IGNORECASE),
    "Buddy Lee Doll": re.compile(r"buddy\s*lee", re.IGNORECASE),
    "Lee": re.compile(r"\blee\b", re.IGNORECASE),
    
    # ========================================================================
    # WRANGLER
    # ========================================================================
    "Wrangler 111MJ Proto": re.compile(r"111mj.*proto|proto.*111mj", re.IGNORECASE),
    "Wrangler 111MJ": re.compile(r"wrangler.*111mj|111mj", re.IGNORECASE),
    "Wrangler 11MJZ": re.compile(r"wrangler.*11mjz", re.IGNORECASE),
    "Wrangler 11MWZ": re.compile(r"wrangler.*11mwz|11mwz", re.IGNORECASE),
    "Wrangler 24MJ": re.compile(r"wrangler.*24mj", re.IGNORECASE),
    "Wrangler Blue Bell": re.compile(r"wrangler.*blue\s*bell|blue\s*bell.*wrangler", re.IGNORECASE),
    "Wrangler": re.compile(r"wrangler", re.IGNORECASE),
    
    # ========================================================================
    # CHINSTRAP - HIGHEST VALUE WORKWEAR (Your insight!)
    # ========================================================================
    "1920s Chinstrap": re.compile(r"chinstrap.*(192|1920)|192.*chinstrap|1920.*chinstrap", re.IGNORECASE),
    "1930s Chinstrap Coverall": re.compile(r"chinstrap.*coverall.*(193|1930)|193.*chinstrap.*coverall|1930.*chinstrap.*coverall|coverall.*chinstrap.*(193|1930)", re.IGNORECASE),
    "1930s Chinstrap": re.compile(r"chinstrap.*(193|1930)|193.*chinstrap|1930.*chinstrap", re.IGNORECASE),
    "1940s Chinstrap": re.compile(r"chinstrap.*(194|1940)|194.*chinstrap|1940.*chinstrap", re.IGNORECASE),
    "Chinstrap": re.compile(r"chinstrap", re.IGNORECASE),
    
    # ========================================================================
    # CARHARTT - ERA + TYPE (Special collectible brand)
    # ========================================================================
    "1930s Carhartt Coverall": re.compile(r"carhartt.*coverall.*(193|1930)|193.*carhartt.*coverall|1930.*carhartt.*coverall", re.IGNORECASE),
    "1940s Carhartt Coverall": re.compile(r"carhartt.*coverall.*(194|1940)|194.*carhartt.*coverall|1940.*carhartt.*coverall", re.IGNORECASE),
    "1950s Carhartt Duck Coverall": re.compile(r"carhartt.*(brown\s*duck|duck).*coverall.*(195|1950)|carhartt.*coverall.*(195|1950)", re.IGNORECASE),
    "1960s Carhartt Coverall": re.compile(r"carhartt.*coverall.*(196|1960)|196.*carhartt.*coverall|1960.*carhartt.*coverall", re.IGNORECASE),
    "1940s Carhartt Overall": re.compile(r"carhartt.*overall.*(194|1940)|194.*carhartt.*overall|1940.*carhartt.*overall", re.IGNORECASE),
    "1950s Carhartt Overall": re.compile(r"carhartt.*overall.*(195|1950)|195.*carhartt.*overall|1950.*carhartt.*overall", re.IGNORECASE),
    "Carhartt Denim": re.compile(r"carhartt.*denim", re.IGNORECASE),
    "Carhartt Brown Duck": re.compile(r"carhartt.*(brown\s*duck|duck)", re.IGNORECASE),
    "Carhartt Detroit": re.compile(r"carhartt.*detroit", re.IGNORECASE),
    "Carhartt": re.compile(r"carhartt", re.IGNORECASE),
    
    # ========================================================================
    # COVERALLS BY ERA (Generic - after Carhartt catches its items)
    # ========================================================================
    "1930s Coverall": re.compile(r"coverall.*(193|1930)|193.*coverall|1930.*coverall", re.IGNORECASE),
    "1940s Coverall": re.compile(r"coverall.*(194|1940)|194.*coverall|1940.*coverall|ww2.*coverall|coverall.*ww2", re.IGNORECASE),
    "1950s Coverall": re.compile(r"coverall.*(195|1950)|195.*coverall|1950.*coverall", re.IGNORECASE),
    "1960s Coverall": re.compile(r"coverall.*(196|1960)|196.*coverall|1960.*coverall", re.IGNORECASE),
    "Coverall": re.compile(r"coverall", re.IGNORECASE),
    
    # ========================================================================
    # OVERALLS BY ERA
    # ========================================================================
    "1930s Overall": re.compile(r"overall.*(193|1930)|193.*overall|1930.*overall", re.IGNORECASE),
    "1940s Overall": re.compile(r"overall.*(194|1940)|194.*overall|1940.*overall", re.IGNORECASE),
    "1950s Overall": re.compile(r"overall.*(195|1950)|195.*overall|1950.*overall", re.IGNORECASE),
    "Overall": re.compile(r"overall", re.IGNORECASE),
    
    # ========================================================================
    # OTHER WORKWEAR BRANDS
    # ========================================================================
    "1930s Hercules": re.compile(r"hercules.*(193|1930)|193.*hercules|1930.*hercules", re.IGNORECASE),
    "Hercules": re.compile(r"hercules", re.IGNORECASE),
    "1940s Head Light": re.compile(r"head\s*light.*(194|1940)|194.*head\s*light|1940.*head\s*light", re.IGNORECASE),
    "Head Light": re.compile(r"head\s*light", re.IGNORECASE),
    "Big Yank Mountain Pocket": re.compile(r"big\s*yank.*mountain\s*pocket", re.IGNORECASE),
    "Big Yank Chambray": re.compile(r"big\s*yank.*chambray", re.IGNORECASE),
    "Big Yank": re.compile(r"big\s*yank", re.IGNORECASE),
    "1930s Big Smith": re.compile(r"big\s*smith.*(193|1930)|193.*big\s*smith|1930.*big\s*smith", re.IGNORECASE),
    "Big Smith": re.compile(r"big\s*smith", re.IGNORECASE),
    "Osh Kosh": re.compile(r"osh\s*kosh|oshkosh", re.IGNORECASE),
    "Sweet-Orr": re.compile(r"sweet-orr", re.IGNORECASE),
    "Pay Day": re.compile(r"pay\s*day", re.IGNORECASE),
    "Finck's": re.compile(r"finck", re.IGNORECASE),
    "Can't Bust'Em": re.compile(r"can't\s*bust|can\'t\s*bust", re.IGNORECASE),
    "Strong Reliable": re.compile(r"strong\s*reliable", re.IGNORECASE),
    "Pointer": re.compile(r"pointer", re.IGNORECASE),
    "Round House": re.compile(r"round\s*house", re.IGNORECASE),
    "Crown Advertising": re.compile(r"crown.*(advertising|clock)", re.IGNORECASE),
    "Crown": re.compile(r"\bcrown\b", re.IGNORECASE),
    
    # ========================================================================
    # WORKWEAR TYPES
    # ========================================================================
    "1930s Painter Pants": re.compile(r"painter.*(193|1930)|193.*painter|1930.*painter", re.IGNORECASE),
    "Painter Pants": re.compile(r"painter", re.IGNORECASE),
    "Engineer Pants": re.compile(r"engineer.*pant", re.IGNORECASE),
    "Double Knee": re.compile(r"double\s*knee", re.IGNORECASE),
    "Chambray Shirt": re.compile(r"chambray", re.IGNORECASE),
    "Buckleback": re.compile(r"buckleback", re.IGNORECASE),
    "Railroad": re.compile(r"railroad", re.IGNORECASE),
    "Denim Work Trousers": re.compile(r"denim.*(trouser|work\s*pant)|denim.*pant", re.IGNORECASE),
    "Work Shirt": re.compile(r"work\s*shirt", re.IGNORECASE),
    
    # ========================================================================
    # CHAMPION - MILITARY VS CIVILIAN
    # ========================================================================
    "Champion Rock Hood": re.compile(r"rock\s*hood", re.IGNORECASE),
    "Champion Afterhoody": re.compile(r"afterhoody|after\s*hoody", re.IGNORECASE),
    "Champion USMA Reverse Weave": re.compile(r"usma.*reverse|reverse.*usma|west\s*point.*reverse", re.IGNORECASE),
    "Champion USAFA Reverse Weave": re.compile(r"usafa.*reverse|reverse.*usafa", re.IGNORECASE),
    "Champion USNA Reverse Weave": re.compile(r"usna.*reverse|reverse.*usna", re.IGNORECASE),
    "Champion Navy Reverse Weave": re.compile(r"(navy|u\.s\.n).*reverse\s*weave|reverse\s*weave.*(navy|u\.s\.n)", re.IGNORECASE),
    "Champion Military Reverse Weave": re.compile(r"(army|air\s*force|coast\s*guard|marines|military).*reverse|reverse.*(army|air\s*force|coast\s*guard|marines)", re.IGNORECASE),
    "Champion W/F Double Face": re.compile(r"champion.*w\/f|double\s*face", re.IGNORECASE),
    "Champion Football Tee Water Print": re.compile(r"champion.*(football|water\s*print).*(water\s*print|football)", re.IGNORECASE),
    "Champion Football Tee": re.compile(r"champion.*football", re.IGNORECASE),
    "Champion Two-Tone": re.compile(r"champion.*two-tone", re.IGNORECASE),
    "Champion Reverse Weave": re.compile(r"reverse\s*weave", re.IGNORECASE),
    "Champion": re.compile(r"champion", re.IGNORECASE),
    
    # ========================================================================
    # SWEATSHIRTS - SPECIFIC TYPES
    # ========================================================================
    "Spalding Sweat": re.compile(r"spalding.*sweat", re.IGNORECASE),
    "Spruce Sweat": re.compile(r"spruce.*sweat", re.IGNORECASE),
    "Beethoven Sweat": re.compile(r"beethoven", re.IGNORECASE),
    "Bach Sweat": re.compile(r"\bbach\b", re.IGNORECASE),
    "Peanuts Schroeder Sweat": re.compile(r"schroeder", re.IGNORECASE),
    "Peanuts Snoopy Sweat": re.compile(r"snoopy|peanuts|charlie\s*brown", re.IGNORECASE),
    "1940s W/V Sweat": re.compile(r"w\/v.*(194|1940)|194.*w\/v|1940.*w\/v", re.IGNORECASE),
    "W/V Sweat": re.compile(r"\bw\/v\b", re.IGNORECASE),
    "S/V Sweat": re.compile(r"\bs\/v\b", re.IGNORECASE),
    "Two-Tone Sweat": re.compile(r"two-?\s*tone.*sweat|sweat.*two-?\s*tone", re.IGNORECASE),
    "Water Print Sweat": re.compile(r"water\s*print", re.IGNORECASE),
    "Flock Print Black Body": re.compile(r"(black.*body|black.*flock).*sweat|flock.*(black|body)|black.*flock", re.IGNORECASE),
    "Flock Print Sweat": re.compile(r"flock", re.IGNORECASE),
    
    # ========================================================================
    # U.S. NAVY - SPECIFIC TYPES
    # ========================================================================
    "Navy WEP Flight Jacket": re.compile(r"wep", re.IGNORECASE),
    "Navy AN-J-2 Summer Flight": re.compile(r"an-j-2|an6551|an6552", re.IGNORECASE),
    "Navy M-421a Flight": re.compile(r"m-421a", re.IGNORECASE),
    "Navy Summer Flight Jacket": re.compile(r"summer.*flight", re.IGNORECASE),
    "Navy G-1 Flight Jacket": re.compile(r"(navy|u\.s\.n).*g-1", re.IGNORECASE),
    "Navy Blue N-1 Deck": re.compile(r"blue.*n-1|n-1.*blue|blue.*deck", re.IGNORECASE),
    "Navy N-1 Pique": re.compile(r"n-1.*pique|pique.*n-1", re.IGNORECASE),
    "Navy A-2 Deck": re.compile(r"a-2.*deck", re.IGNORECASE),
    "Navy N-1 Deck": re.compile(r"n-1", re.IGNORECASE),
    "Navy Dungaree Jacket": re.compile(r"(navy|usn).*dungaree.*jacket", re.IGNORECASE),
    "Navy Dungaree Pants": re.compile(r"(navy|usn).*dungaree.*(trouser|pants)", re.IGNORECASE),
    "Navy P-Coat": re.compile(r"navy.*p-coat|pea\s*coat", re.IGNORECASE),
    "Navy HBT": re.compile(r"(navy|usn).*hbt", re.IGNORECASE),
    "Navy": re.compile(r"u\.s\.navy|u\.s\.n\b|usn\b", re.IGNORECASE),
    
    # ========================================================================
    # USMC
    # ========================================================================
    "USMC Frog Skin Camo": re.compile(r"(usmc|marine).*frog|duck.*hunter|frog\s*skin", re.IGNORECASE),
    "USMC P-44 Monkey Pants": re.compile(r"p-44.*(trouser|pants|hbt)", re.IGNORECASE),
    "USMC P-41/P-42/P-44": re.compile(r"p-4[124]", re.IGNORECASE),
    "USMC": re.compile(r"usmc|marine", re.IGNORECASE),
    
    # ========================================================================
    # USAF
    # ========================================================================
    "USAF G-1 Linecrewman": re.compile(r"g-1.*linecrewman|linecrewman", re.IGNORECASE),
    "USAF L-2A": re.compile(r"l-2a", re.IGNORECASE),
    "USAF L-2B": re.compile(r"l-2b", re.IGNORECASE),
    "USAF MA-1": re.compile(r"ma-1", re.IGNORECASE),
    "USAF B-15": re.compile(r"b-15", re.IGNORECASE),
    "USAF B-10": re.compile(r"b-10", re.IGNORECASE),
    "USAF A-2": re.compile(r"usaf.*a-2|a-2.*flight", re.IGNORECASE),
    "USAF": re.compile(r"usaf|u\.s\.air\s*force", re.IGNORECASE),
    
    # ========================================================================
    # OTHER MILITARY
    # ========================================================================
    "Denim N-3B": re.compile(r"denim.*n-3b|n-3b.*denim", re.IGNORECASE),
    "Army M-65": re.compile(r"m-65", re.IGNORECASE),
    "Army M-43/M-48/M-51": re.compile(r"m-43|m-48|m-51", re.IGNORECASE),
    "Army Tanker": re.compile(r"tanker\s*jacket", re.IGNORECASE),
    "Army HBT": re.compile(r"army.*hbt", re.IGNORECASE),
    "Army": re.compile(r"u\.s\.army", re.IGNORECASE),
    "G-1 Flight Jacket": re.compile(r"g-1", re.IGNORECASE),
    "Coast Guard": re.compile(r"coast\s*guard", re.IGNORECASE),
    
    # ========================================================================
    # PATAGONIA - SPECIFIC PRODUCT LINES
    # ========================================================================
    "Patagonia MARS": re.compile(r"patagonia.*mars|mars.*patagonia", re.IGNORECASE),
    "Patagonia DAS Parka": re.compile(r"patagonia.*das", re.IGNORECASE),
    "Patagonia Glissade": re.compile(r"patagonia.*glissade|glissade", re.IGNORECASE),
    "Patagonia Snap-T": re.compile(r"patagonia.*snap-t|snap-t", re.IGNORECASE),
    "Patagonia Puff/Down": re.compile(r"patagonia.*(puff|down)", re.IGNORECASE),
    "Patagonia Retro": re.compile(r"patagonia.*retro", re.IGNORECASE),
    "Patagonia Synchilla": re.compile(r"patagonia.*synchilla", re.IGNORECASE),
    "Patagonia": re.compile(r"patagonia", re.IGNORECASE),
    
    # ========================================================================
    # OTHER OUTDOOR
    # ========================================================================
    "L.L.Bean Leather Handle Tote": re.compile(r"l\.?l\.?\s*bean.*leather.*handle", re.IGNORECASE),
    "L.L.Bean Tote": re.compile(r"l\.?l\.?\s*bean.*tote", re.IGNORECASE),
    "L.L.Bean Warden": re.compile(r"l\.?l\.?\s*bean.*warden", re.IGNORECASE),
    "L.L.Bean": re.compile(r"l\.?l\.?\s*bean", re.IGNORECASE),
    "North Face Down": re.compile(r"north\s*face.*(down|brooks\s*range|expedition)", re.IGNORECASE),
    "North Face": re.compile(r"north\s*face", re.IGNORECASE),
    "Eddie Bauer Down": re.compile(r"eddie\s*bauer.*(down|leather.*down)", re.IGNORECASE),
    "Eddie Bauer": re.compile(r"eddie\s*bauer", re.IGNORECASE),
    "REI Down": re.compile(r"rei.*(down|expedition)", re.IGNORECASE),
    "REI": re.compile(r"\brei\b", re.IGNORECASE),
    "Gerry Down": re.compile(r"gerry.*(down|expedition)", re.IGNORECASE),
    "Gerry": re.compile(r"\bgerry\b", re.IGNORECASE),
    "Brown's Beach": re.compile(r"brown'?s?\s*beach", re.IGNORECASE),
    "Sierra Designs": re.compile(r"sierra\s*designs", re.IGNORECASE),
    "Filson": re.compile(r"filson", re.IGNORECASE),
    "Barbour": re.compile(r"barbour", re.IGNORECASE),
    "Down Parka": re.compile(r"down.*(parka|jacket)|expedition.*down", re.IGNORECASE),
    
    # ========================================================================
    # SOUVENIR JACKETS - LOCATION SPECIFIC
    # ========================================================================
    "Okinawa Souvenir": re.compile(r"okinawa", re.IGNORECASE),
    "Korea Souvenir": re.compile(r"korea.*souvenir|korea.*jacket", re.IGNORECASE),
    "Alaska Souvenir": re.compile(r"alaska.*souvenir|alaska.*jacket", re.IGNORECASE),
    "Panama Souvenir": re.compile(r"panama", re.IGNORECASE),
    "Vietnam Souvenir": re.compile(r"viet-?nam.*souvenir", re.IGNORECASE),
    "Vietnam Tour": re.compile(r"viet-?nam.*tour", re.IGNORECASE),
    "Misawa Souvenir": re.compile(r"misawa", re.IGNORECASE),
    "Japan Tiger Souvenir": re.compile(r"japan.*tiger|tiger.*japan", re.IGNORECASE),
    "Japan Corduroy Souvenir": re.compile(r"corduroy.*souvenir", re.IGNORECASE),
    "Japan Souvenir": re.compile(r"japan.*souvenir|sukajan", re.IGNORECASE),
    "Souvenir Jacket": re.compile(r"souvenir", re.IGNORECASE),
    
    # ========================================================================
    # SHIRTS BY ERA + TYPE (Your clarification insight)
    # ========================================================================
    "1950s Corduroy Shirt": re.compile(r"corduroy.*(shirt|box).*(195|1950)|195.*corduroy|1950.*corduroy", re.IGNORECASE),
    "1960s Corduroy Shirt": re.compile(r"corduroy.*(shirt|box).*(196|1960)|196.*corduroy|1960.*corduroy", re.IGNORECASE),
    "Corduroy Shirt": re.compile(r"corduroy.*(shirt|box)", re.IGNORECASE),
    "1950s Rayon Shirt": re.compile(r"rayon.*(195|1950)|195.*rayon|1950.*rayon", re.IGNORECASE),
    "1960s Shadow Plaid Rayon": re.compile(r"(shadow.*plaid|arrow).*(196|1960)|(196|1960).*(shadow.*plaid|arrow)", re.IGNORECASE),
    "Rayon Shirt": re.compile(r"rayon", re.IGNORECASE),
    "Shadow Plaid Shirt": re.compile(r"shadow\s*plaid|shadow.*check", re.IGNORECASE),
    "1950s Pilgrim Flannel": re.compile(r"pilgrim.*(195|1950)|195.*pilgrim|1950.*pilgrim", re.IGNORECASE),
    "1950s Sun Valley Flannel": re.compile(r"sun\s*valley.*(195|1950)|195.*sun\s*valley|1950.*sun\s*valley", re.IGNORECASE),
    "Pilgrim Flannel": re.compile(r"pilgrim", re.IGNORECASE),
    "Sun Valley Flannel": re.compile(r"sun\s*valley", re.IGNORECASE),
    "Flannel Shirt": re.compile(r"flannel", re.IGNORECASE),
    "Western Shirt": re.compile(r"western.*shirt", re.IGNORECASE),
    "Wool Shirt": re.compile(r"wool.*shirt", re.IGNORECASE),
    "Arrow Shirt": re.compile(r"\barrow\b", re.IGNORECASE),
    
    # ========================================================================
    # HAWAIIAN
    # ========================================================================
    "Kamehameha": re.compile(r"kamehameha", re.IGNORECASE),
    "Kahanamoku": re.compile(r"kahanamoku", re.IGNORECASE),
    "Hawaiian/Aloha": re.compile(r"hawaiian|aloha", re.IGNORECASE),
    
    # ========================================================================
    # TEES - BRAND SPECIFIC
    # ========================================================================
    "Band Tee": re.compile(r"(dinosaur|nirvana|metallica|grateful\s*dead|rolling\s*stones|beatles|zeppelin|hendrix|floyd|ramones|clash|pistols|sabbath|maiden|motorhead|slayer|megadeth|pantera|soundgarden|pearl\s*jam|alice\s*in\s*chains|red\s*hot|radio\s*head|guns.*roses|ac\/dc|kiss\b|van\s*halen|ozzy).*t-shirt|tour.*t-shirt", re.IGNORECASE),
    "Harley-Davidson Tee": re.compile(r"harley.*t-shirt|harley.*tee", re.IGNORECASE),
    "Stussy Tee": re.compile(r"stussy.*t-shirt", re.IGNORECASE),
    "Stussy": re.compile(r"stussy", re.IGNORECASE),
    "Nike Tee": re.compile(r"nike.*t-shirt", re.IGNORECASE),
    "Photo Print Tee": re.compile(r"photo.*print|bruce\s*weber", re.IGNORECASE),
    "Vintage T-Shirt": re.compile(r"t-shirt", re.IGNORECASE),
    
    # ========================================================================
    # SWEATERS
    # ========================================================================
    "Mohair Cardigan": re.compile(r"mohair", re.IGNORECASE),
    "Cowichan": re.compile(r"cowichan", re.IGNORECASE),
    "Pendleton": re.compile(r"pendleton", re.IGNORECASE),
    "McGregor": re.compile(r"mcgregor", re.IGNORECASE),
    
    # ========================================================================
    # NATIVE/SOUTHWEST
    # ========================================================================
    "Navajo/Chimayo": re.compile(r"navajo|chimayo|ortega", re.IGNORECASE),
    
    # ========================================================================
    # LEATHER
    # ========================================================================
    "Buco Leather": re.compile(r"\bbuco\b", re.IGNORECASE),
    "Schott Leather": re.compile(r"schott", re.IGNORECASE),
    "Horsehide": re.compile(r"horsehide", re.IGNORECASE),
    "Car Coat": re.compile(r"car\s*coat", re.IGNORECASE),
    "Cafe Racer": re.compile(r"cafe\s*racer", re.IGNORECASE),
    
    # ========================================================================
    # SHOES
    # ========================================================================
    "Nike Shoes": re.compile(r"nike.*(shoes|sneaker|jordan|dunk|cortez|waffle|blazer|vandal)", re.IGNORECASE),
    "Nike": re.compile(r"\bnike\b", re.IGNORECASE),
    "Converse": re.compile(r"converse", re.IGNORECASE),
    "Red Wing Boots": re.compile(r"red\s*wing", re.IGNORECASE),
    "Vans": re.compile(r"\bvans\b", re.IGNORECASE),
    
    # ========================================================================
    # ACCESSORIES
    # ========================================================================
    "Advertising": re.compile(r"advertising|banner|display|wall\s*clock", re.IGNORECASE),
    "Tote Bag": re.compile(r"tote", re.IGNORECASE),
    "Quilt": re.compile(r"quilt", re.IGNORECASE),
    "Navajo Rug": re.compile(r"navajo.*rug|swastika.*rug|\brug\b", re.IGNORECASE),
    "Backpack": re.compile(r"back\s*pack|backpack|gregory|chouinard|kelty", re.IGNORECASE),
    "Blanket": re.compile(r"\bblanket\b", re.IGNORECASE),
    
    # ========================================================================
    # JACKET TYPES
    # ========================================================================
    "Award/Varsity Jacket": re.compile(r"award\s*jacket|varsity|letterman", re.IGNORECASE),
    "Drizzler Jacket": re.compile(r"drizzler", re.IGNORECASE),
    "Western Jacket": re.compile(r"western.*jacket|gabardine.*jacket", re.IGNORECASE),
    "Mackinaw": re.compile(r"mackinaw", re.IGNORECASE),
    
    # ========================================================================
    # CATCH-ALLS
    # ========================================================================
    "Denim Jacket": re.compile(r"denim\s*jacket", re.IGNORECASE),
    "Denim Jeans": re.compile(r"jeans", re.IGNORECASE),
    "Gore-Tex": re.compile(r"gore-tex", re.IGNORECASE),
    "Corduroy": re.compile(r"corduroy", re.IGNORECASE),
    "Leather Jacket": re.compile(r"leather.*jacket", re.IGNORECASE),
    "Parka": re.compile(r"parka", re.IGNORECASE),
    "Cardigan": re.compile(r"cardigan", re.IGNORECASE),
    "Vest": re.compile(r"\bvest\b", re.IGNORECASE),
    "Sweatshirt": re.compile(r"sweat", re.IGNORECASE),
    "WW2 Military": re.compile(r"ww2|wwii", re.IGNORECASE),
    "Jacket": re.compile(r"jacket", re.IGNORECASE),
    "Shirt": re.compile(r"shirt", re.IGNORECASE),
    "Trousers": re.compile(r"trouser|pants", re.IGNORECASE),
}

# Trends caching
trends_cache = {}
CACHE_DURATION = 3600  # 1 hour in seconds

def clear_trends_cache():
    """Clear the trends cache (call after new data is scraped)"""
    global trends_cache
    trends_cache = {}
    print(f"[{datetime.now()}] Trends cache cleared")

def get_cached_trends():
    """Get cached trends if available and not expired"""
    if not trends_cache:
        return None
    
    now = time.time()
    if now - trends_cache.get('timestamp', 0) > CACHE_DURATION:
        return None
    
    return trends_cache.get('data')

def set_cached_trends(data):
    """Cache trends data with timestamp"""
    global trends_cache
    trends_cache = {
        'data': data,
        'timestamp': time.time()
    }

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


# ========== TRENDS ENDPOINTS ==========

@app.get("/api/trends", response_model=TrendsResponse)
def get_trends(db: Session = Depends(get_db)):
    """Get market trends analysis with caching"""
    # Check cache first
    cached = get_cached_trends()
    if cached:
        return cached
    
    # Calculate trends
    trends_data = calculate_trends(db)
    
    # Cache the result
    set_cached_trends(trends_data)
    
    return trends_data


def calculate_trends(db: Session) -> TrendsResponse:
    """Calculate market trends by comparing this month vs trailing 6 months"""
    now = datetime.now()
    this_month = date(now.year, now.month, 1)
    six_months_ago = date(now.year, now.month - 6, 1) if now.month > 6 else date(now.year - 1, now.month + 6, 1)
    
    # Get all items
    all_items = db.query(Item).all()
    
    # Categorize each item
    categorized = []
    for item in all_items:
        sold_date = item.sold_date
        category = 'Other'
        
        for cat, regex in CATEGORIES.items():
            if regex.search(item.title):
                category = cat
                break
        
        categorized.append({
            'item': item,
            'category': category,
            'sold_date': sold_date
        })
    
    # Split into this month vs trailing 6 months
    this_month_items = [c for c in categorized if c['sold_date'] >= this_month]
    trailing_items = [c for c in categorized if c['sold_date'] >= six_months_ago and c['sold_date'] < this_month]
    
    # Calculate stats per category
    stats = {}
    for cat in CATEGORIES.keys():
        recent = [c for c in this_month_items if c['category'] == cat]
        trailing = [c for c in trailing_items if c['category'] == cat]
        
        recent_avg_price = sum(c['item'].price_usd for c in recent) / len(recent) if recent else 0
        trailing_avg_price = sum(c['item'].price_usd for c in trailing) / len(trailing) if trailing else 0
        
        # Monthly average volume over 6 months
        trailing_monthly_vol = len(trailing) / 6
        
        price_change = ((recent_avg_price - trailing_avg_price) / trailing_avg_price * 100) if trailing_avg_price > 0 else 0
        volume_change = ((len(recent) - trailing_monthly_vol) / trailing_monthly_vol * 100) if trailing_monthly_vol > 0 else (100 if recent else 0)
        
        stats[cat] = {
            'recent_count': len(recent),
            'trailing_count': len(trailing),
            'trailing_monthly_avg': trailing_monthly_vol,
            'recent_avg_price': round(recent_avg_price),
            'trailing_avg_price': round(trailing_avg_price),
            'price_change': round(price_change),
            'volume_change': round(volume_change)
        }
    
    # Filter for meaningful categories (need baseline data AND at least 1 item this month for price comparison)
    valid_for_price = [(cat, s) for cat, s in stats.items() if s['trailing_count'] >= 3 and s['recent_count'] >= 1]
    valid_for_price.sort(key=lambda x: x[1]['price_change'], reverse=True)
    
    # Hot categories (price up > 20%)
    hot_categories = [{'name': cat, **s} for cat, s in valid_for_price if s['price_change'] > 20][:5]
    
    # Cold categories (price down < -20%)
    cold_categories = [{'name': cat, **s} for cat, s in valid_for_price if s['price_change'] < -20][:5]
    
    # Volume changes
    by_volume = [(cat, s) for cat, s in stats.items() if s['trailing_count'] >= 3]
    by_volume.sort(key=lambda x: x[1]['volume_change'], reverse=True)
    
    volume_up = [{'name': cat, **s} for cat, s in by_volume if s['volume_change'] > 50 and s['recent_count'] >= 2][:3]
    volume_down = [{'name': cat, **s} for cat, s in by_volume if s['recent_count'] == 0 and s['trailing_monthly_avg'] >= 1][:5]
    
    # All active categories
    active_categories = [{'name': cat, **s} for cat, s in valid_for_price][:20]
    
    # Generate summary
    summary_parts = []
    if hot_categories:
        top_hot = [f"{cat['name']} (+{cat['price_change']}%)" for cat in hot_categories[:2]]
        summary_parts.append(f"Prices up on: {', '.join(top_hot)}.")
    
    if cold_categories:
        top_cold = [f"{cat['name']} ({cat['price_change']}%)" for cat in cold_categories[:2]]
        summary_parts.append(f"Prices down on: {', '.join(top_cold)}.")
    
    if volume_down:
        not_listed = [cat['name'] for cat in volume_down[:3]]
        summary_parts.append(f"Not listing this month: {', '.join(not_listed)}.")
    
    total_recent = sum(s['recent_count'] for s in stats.values())
    total_trailing_avg = sum(s['trailing_monthly_avg'] for s in stats.values())
    summary_parts.append(f"This month: {total_recent} items sold (avg {round(total_trailing_avg)}/mo).")
    
    summary = " ".join(summary_parts) if summary_parts else "Market appears stable this month."
    
    return TrendsResponse(
        summary=summary,
        hot_categories=hot_categories,
        cold_categories=cold_categories,
        volume_changes=volume_up + volume_down,
        all_categories=active_categories,
        stats={
            'total_categories': len(CATEGORIES),
            'active_categories': len(valid_for_price),
            'this_month_items': len(this_month_items),
            'trailing_items': len(trailing_items)
        },
        generated_at=datetime.now().isoformat()
    )


@app.get("/api/trends/test", response_model=CategoryTestResponse)
def test_category(title: str = Query(..., description="Item title to test categorization")):
    """Test which category an item title matches"""
    matches = []
    assigned_category = 'Other'
    
    for cat, regex in CATEGORIES.items():
        if regex.search(title):
            matches.append({
                'category': cat,
                'pattern': regex.pattern
            })
            if assigned_category == 'Other':
                assigned_category = cat
    
    return CategoryTestResponse(
        title=title,
        category=assigned_category,
        matches=matches
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
        # Clear cache after manual scrape too
        clear_trends_cache()
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
