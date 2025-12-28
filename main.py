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
CATEGORIES = {
    # === LEVI'S JEANS ===
    "Levi's S501XX (WW2)": re.compile(r"levi.*s501|s501xx", re.IGNORECASE),
    "Levi's 501XX Buckleback": re.compile(r"levi.*501.*buckleback", re.IGNORECASE),
    "Levi's 501XX": re.compile(r"levi.*501.*xx|levi.*501zxx", re.IGNORECASE),
    "Levi's 501 Big E": re.compile(r"levi.*501.*big\s*e|levi.*501e\b", re.IGNORECASE),
    "Levi's 501 66 Single": re.compile(r"levi.*501.*66", re.IGNORECASE),
    "Levi's 501 (Other)": re.compile(r"levi.*501", re.IGNORECASE),
    "Levi's 502 Big E": re.compile(r"levi.*502.*big\s*e|levi.*502e\b", re.IGNORECASE),
    "Levi's 505 Big E": re.compile(r"levi.*505.*big\s*e|levi.*505e\b", re.IGNORECASE),
    "Levi's 505 66 Single": re.compile(r"levi.*505.*66|levi.*505ss", re.IGNORECASE),
    "Levi's 606 Big E": re.compile(r"levi.*606", re.IGNORECASE),
    "Levi's 517 Jeans": re.compile(r"levi.*\b517\b(?!xx)", re.IGNORECASE),
    "Levi's 518": re.compile(r"levi.*\b518\b", re.IGNORECASE),
    "Levi's 701 (Women's)": re.compile(r"levi.*\b701\b", re.IGNORECASE),
    "Levi's 551ZXX": re.compile(r"levi.*551", re.IGNORECASE),
    "Levi's 503ZXX/BXX": re.compile(r"levi.*503", re.IGNORECASE),
    "Levi's 550ZXX": re.compile(r"levi.*550", re.IGNORECASE),
    
    # === LEVI'S JACKETS ===
    "Levi's 213 (Pre-Type1)": re.compile(r"levi.*\b213\b", re.IGNORECASE),
    "Levi's S506XX (WW2)": re.compile(r"s506xx", re.IGNORECASE),
    "Levi's 506XXE (Big E)": re.compile(r"506xxe", re.IGNORECASE),
    "Levi's 506XX (1st)": re.compile(r"506xx", re.IGNORECASE),
    "Levi's 507XX (2nd)": re.compile(r"levi.*507.*xx|levi.*507bxx", re.IGNORECASE),
    "Levi's 517XX": re.compile(r"levi.*517xx", re.IGNORECASE),
    "Levi's 519XX (Blanket)": re.compile(r"levi.*519", re.IGNORECASE),
    "Levi's 557XX": re.compile(r"levi.*557.*xx|levi.*557e\b", re.IGNORECASE),
    "Levi's 557 (3rd)": re.compile(r"levi.*\b557\b", re.IGNORECASE),
    "Levi's 558XX": re.compile(r"levi.*558.*xx|levi.*558e\b", re.IGNORECASE),
    "Levi's 559XX": re.compile(r"levi.*559.*xx|levi.*559e\b", re.IGNORECASE),
    "Levi's 70505-0117": re.compile(r"70505-0117", re.IGNORECASE),
    "Levi's 70505 (3rd)": re.compile(r"levi.*70505", re.IGNORECASE),
    "Levi's 70506": re.compile(r"levi.*70506", re.IGNORECASE),
    "Levi's 70507": re.compile(r"levi.*70507", re.IGNORECASE),
    "Levi's 71205 Boa": re.compile(r"levi.*71205|levi.*boa", re.IGNORECASE),
    "Levi's 71506": re.compile(r"levi.*71506", re.IGNORECASE),
    "Levi's Shorthorn": re.compile(r"levi.*shorthorn", re.IGNORECASE),
    "Levi's Saddleman": re.compile(r"levi.*saddleman", re.IGNORECASE),
    "Levi's Advertising": re.compile(r"levi.*advertising|levi.*banner", re.IGNORECASE),
    "Levi's Other": re.compile(r"levi", re.IGNORECASE),
    
    # === LEE ===
    "Lee 191-J": re.compile(r"lee.*191-?j", re.IGNORECASE),
    "Lee S91-J (WW2)": re.compile(r"lee.*s91-?j", re.IGNORECASE),
    "Lee 91-LJ": re.compile(r"lee.*91-?lj", re.IGNORECASE),
    "Lee 91-J Chore": re.compile(r"lee.*91-j|lee.*91j", re.IGNORECASE),
    "Lee 101-J Jacket": re.compile(r"lee.*101-?j\b", re.IGNORECASE),
    "Lee 98-J": re.compile(r"lee.*98-?j", re.IGNORECASE),
    "Lee 91-B": re.compile(r"lee.*91-b", re.IGNORECASE),
    "Lee 91-SB": re.compile(r"lee.*91-sb", re.IGNORECASE),
    "Lee 81-LJ": re.compile(r"lee.*81-?lj", re.IGNORECASE),
    "Lee 44-J": re.compile(r"lee.*44-?j", re.IGNORECASE),
    "Lee 101Z": re.compile(r"lee.*101z", re.IGNORECASE),
    "Lee 101B Cowboy": re.compile(r"lee.*101b|lee.*cowboy", re.IGNORECASE),
    "Lee 191": re.compile(r"lee.*\b191\b", re.IGNORECASE),
    "Lee 220": re.compile(r"lee.*220", re.IGNORECASE),
    "Lee Westerner": re.compile(r"lee.*westerner", re.IGNORECASE),
    "Lee Riders": re.compile(r"lee.*riders", re.IGNORECASE),
    "Lee Storm Rider": re.compile(r"lee.*storm\s*rider", re.IGNORECASE),
    "Lee White Canvas": re.compile(r"lee.*white.*canvas", re.IGNORECASE),
    "Lee Coverall": re.compile(r"lee.*coverall", re.IGNORECASE),
    "Lee HBT": re.compile(r"lee.*hbt", re.IGNORECASE),
    "Lee Princeton": re.compile(r"lee.*princeton", re.IGNORECASE),
    "Lee Jelt": re.compile(r"lee.*jelt", re.IGNORECASE),
    "Lee Frisco": re.compile(r"lee.*frisco", re.IGNORECASE),
    "Lee Leens": re.compile(r"lee.*leens", re.IGNORECASE),
    "Lee Painter": re.compile(r"lee.*painter", re.IGNORECASE),
    "Lee Boss/Logger": re.compile(r"lee.*boss|lee.*logger", re.IGNORECASE),
    "Lee Advertising": re.compile(r"lee.*advertising|lee.*sign", re.IGNORECASE),
    "Buddy Lee Doll": re.compile(r"buddy\s*lee", re.IGNORECASE),
    "Lee Other": re.compile(r"\blee\b", re.IGNORECASE),
    
    # === WRANGLER ===
    "Wrangler 111MJ Proto": re.compile(r"111mj.*proto|proto.*111mj", re.IGNORECASE),
    "Wrangler 111MJ": re.compile(r"wrangler.*111mj|111mj", re.IGNORECASE),
    "Wrangler 11MJZ": re.compile(r"wrangler.*11mjz", re.IGNORECASE),
    "Wrangler 11MWZ": re.compile(r"wrangler.*11mwz|11mwz", re.IGNORECASE),
    "Wrangler 11MW": re.compile(r"wrangler.*11mw\b", re.IGNORECASE),
    "Wrangler 24MJ": re.compile(r"wrangler.*24mj", re.IGNORECASE),
    "Wrangler 27MW": re.compile(r"wrangler.*27mw", re.IGNORECASE),
    "Wrangler 77MJZ": re.compile(r"wrangler.*77mjz", re.IGNORECASE),
    "Wrangler 124MJ": re.compile(r"wrangler.*124mj", re.IGNORECASE),
    "Wrangler 12MJZ": re.compile(r"wrangler.*12mjz", re.IGNORECASE),
    "Wrangler 888MJ": re.compile(r"wrangler.*888mj", re.IGNORECASE),
    "Wrangler Blue Bell": re.compile(r"wrangler.*blue\s*bell|blue\s*bell.*wrangler", re.IGNORECASE),
    "Wrangler Other": re.compile(r"wrangler", re.IGNORECASE),
    
    # === CHAMPION ===
    "Champion Rock Hood": re.compile(r"rock\s*hood", re.IGNORECASE),
    "Champion Afterhoody": re.compile(r"afterhoody|after\s*hoody", re.IGNORECASE),
    "Champion W/F Double Face": re.compile(r"champion.*w\/f|double\s*face", re.IGNORECASE),
    "Champion USMA": re.compile(r"usma|west\s*point", re.IGNORECASE),
    "Champion USAFA": re.compile(r"usafa|air\s*force\s*academy", re.IGNORECASE),
    "Champion USNA": re.compile(r"usna|naval\s*academy", re.IGNORECASE),
    "Champion Kings Point": re.compile(r"kings\s*point", re.IGNORECASE),
    "Champion Coast Guard": re.compile(r"champion.*coast\s*guard|coast\s*guard.*reverse", re.IGNORECASE),
    "Champion Reverse Weave": re.compile(r"reverse\s*weave", re.IGNORECASE),
    "Champion Football Tee": re.compile(r"champion.*football", re.IGNORECASE),
    "Champion Flock Print": re.compile(r"champion.*flock", re.IGNORECASE),
    "Champion Water Print": re.compile(r"champion.*water", re.IGNORECASE),
    "Champion Two-Tone": re.compile(r"champion.*two-tone", re.IGNORECASE),
    "Champion Other": re.compile(r"champion", re.IGNORECASE),
    
    # === VINTAGE SWEATSHIRTS ===
    "Spalding Sweat": re.compile(r"spalding", re.IGNORECASE),
    "Duxbak Sweat": re.compile(r"duxbak", re.IGNORECASE),
    "Spruce Sweat": re.compile(r"spruce", re.IGNORECASE),
    "Bodygard Sweat": re.compile(r"bodygard", re.IGNORECASE),
    "Akom Sweat": re.compile(r"akom", re.IGNORECASE),
    "Russell Sweat": re.compile(r"russell", re.IGNORECASE),
    "Two-Tone Sweat": re.compile(r"two-?\s*tone.*sweat|sweat.*two-?\s*tone", re.IGNORECASE),
    "Water Print Sweat": re.compile(r"water\s*print", re.IGNORECASE),
    "Composer Print": re.compile(r"beethoven|bach|brahms|nietzsche", re.IGNORECASE),
    "Peanuts/Snoopy": re.compile(r"peanuts|snoopy|charlie\s*brown|schroeder|pig\s*pen", re.IGNORECASE),
    "W/V Sweat": re.compile(r"w\/v", re.IGNORECASE),
    "S/V Sweat": re.compile(r"s\/v", re.IGNORECASE),
    "Sweat Hoody": re.compile(r"sweat\s*hoody|s\/f\s*sweat", re.IGNORECASE),
    "Flock Print (Black Body)": re.compile(r"black.*flock|flock.*black\s*body", re.IGNORECASE),
    "Flock Print": re.compile(r"flock\s*print|flock", re.IGNORECASE),
    "Sweatshirt": re.compile(r"sweat\s*shirt|sweatshirt", re.IGNORECASE),
    
    # === U.S. NAVY ===
    "Navy Blue N-1 Deck": re.compile(r"blue.*n-1|n-1.*blue|blue.*deck", re.IGNORECASE),
    "Navy N-1 Pique": re.compile(r"n-1.*pique|pique.*n-1", re.IGNORECASE),
    "Navy A-2 Deck": re.compile(r"a-2.*deck", re.IGNORECASE),
    "Navy N-1 Deck": re.compile(r"n-1.*deck|n-1\b", re.IGNORECASE),
    "Navy Dangaree (Shawl)": re.compile(r"dangaree", re.IGNORECASE),
    "Navy Dungaree Jacket": re.compile(r"navy.*dungaree.*jacket|usn.*dungaree.*jacket", re.IGNORECASE),
    "Navy Dungaree Pants": re.compile(r"navy.*dungaree.*(trouser|pants)|usn.*dungaree.*(trouser|pants)", re.IGNORECASE),
    "Navy NAF-1168": re.compile(r"naf-1168", re.IGNORECASE),
    "Navy Summer Flight Jacket": re.compile(r"an-j-2|an6551|an6552|m-421a|summer.*flight", re.IGNORECASE),
    "Navy Flight Jacket": re.compile(r"navy.*flight|u\.s\.n.*flight", re.IGNORECASE),
    "Navy P-Coat": re.compile(r"navy.*p-coat|navy.*pea\s*coat", re.IGNORECASE),
    "Navy Gunner Smock": re.compile(r"gunner\s*smock", re.IGNORECASE),
    "Navy HBT": re.compile(r"navy.*hbt|usn.*hbt", re.IGNORECASE),
    "Navy Catapult Jacket": re.compile(r"catapult", re.IGNORECASE),
    "Navy Sweat": re.compile(r"navy.*sweat", re.IGNORECASE),
    "Navy Other": re.compile(r"u\.s\.navy|u\.s\.n\b", re.IGNORECASE),
    
    # === U.S. COAST GUARD ===
    "Coast Guard": re.compile(r"coast\s*guard", re.IGNORECASE),
    
    # === ROYAL NAVY (British) ===
    "Royal Navy": re.compile(r"royal\s*navy", re.IGNORECASE),
    
    # === U.S. ARMY ===
    "Army M-65": re.compile(r"m-65", re.IGNORECASE),
    "Army M-43/M-48/M-51": re.compile(r"m-43|m-48|m-51", re.IGNORECASE),
    "Army Tanker": re.compile(r"tanker\s*jacket", re.IGNORECASE),
    "Army Dungaree/Denim": re.compile(r"army.*dungaree|army.*denim", re.IGNORECASE),
    "Army Jungle Jacket": re.compile(r"jungle", re.IGNORECASE),
    "Army Khaki/Chino": re.compile(r"army.*khaki|army.*chino", re.IGNORECASE),
    "Army Mackinaw": re.compile(r"army.*mackinaw", re.IGNORECASE),
    "Army HBT": re.compile(r"army.*hbt", re.IGNORECASE),
    "Army Daisy Mae Hat": re.compile(r"daisy\s*mae", re.IGNORECASE),
    "Army Snow Parka": re.compile(r"army.*snow|army.*parka", re.IGNORECASE),
    "Army Other": re.compile(r"u\.s\.army", re.IGNORECASE),
    
    # === USMC ===
    "USMC P-41/P-42/P-44": re.compile(r"p-4[124]", re.IGNORECASE),
    "USMC Other": re.compile(r"usmc", re.IGNORECASE),
    
    # === USAF ===
    "USAF G-1 Linecrewman": re.compile(r"g-1.*linecrewman|linecrewman.*jacket", re.IGNORECASE),
    "USAF MA-1": re.compile(r"ma-1", re.IGNORECASE),
    "USAF L-2/L-2B": re.compile(r"l-2[ab]?(?:\b|$)", re.IGNORECASE),
    "USAF B-15/B-10": re.compile(r"b-15|b-10", re.IGNORECASE),
    "USAF A-2": re.compile(r"usaa?f.*a-2|a-2.*flight", re.IGNORECASE),
    "USAF CWU": re.compile(r"cwu", re.IGNORECASE),
    "USAF Other": re.compile(r"usaf|u\.s\.air\s*force", re.IGNORECASE),
    
    # === OTHER MILITARY ===
    "G-1 Flight Jacket": re.compile(r"g-1", re.IGNORECASE),
    "Tiger Stripe Camo": re.compile(r"tiger\s*stripe", re.IGNORECASE),
    "PCU/ECWCS": re.compile(r"pcu|ecwcs|level\s*\d.*jacket", re.IGNORECASE),
    "Military Surplus": re.compile(r"military|bdu", re.IGNORECASE),
    
    # === WORKWEAR BRANDS ===
    "Carhartt Brown Duck": re.compile(r"carhartt.*brown\s*duck", re.IGNORECASE),
    "Carhartt Salt & Pepper": re.compile(r"carhartt.*salt.*pepper", re.IGNORECASE),
    "Carhartt Denim": re.compile(r"carhartt.*denim", re.IGNORECASE),
    "Carhartt Detroit": re.compile(r"carhartt.*detroit", re.IGNORECASE),
    "Carhartt": re.compile(r"carhartt", re.IGNORECASE),
    "Hercules": re.compile(r"hercules", re.IGNORECASE),
    "Head Light": re.compile(r"head\s*light", re.IGNORECASE),
    "Big Yank Mountain Pocket": re.compile(r"big\s*yank.*mountain\s*pocket", re.IGNORECASE),
    "Big Yank Chambray": re.compile(r"big\s*yank.*chambray", re.IGNORECASE),
    "Big Yank Flannel": re.compile(r"big\s*yank.*flannel", re.IGNORECASE),
    "Big Yank": re.compile(r"big\s*yank", re.IGNORECASE),
    "Big Mac": re.compile(r"big\s*mac", re.IGNORECASE),
    "Big Smith": re.compile(r"big\s*smith", re.IGNORECASE),
    "Osh Kosh": re.compile(r"osh\s*kosh|oshkosh", re.IGNORECASE),
    "Sweet-Orr": re.compile(r"sweet-orr", re.IGNORECASE),
    "Super Pay Day": re.compile(r"super\s*pay\s*day", re.IGNORECASE),
    "Pay Day": re.compile(r"pay\s*day", re.IGNORECASE),
    "Finck's": re.compile(r"finck", re.IGNORECASE),
    "Can't Bust'Em": re.compile(r"can't\s*bust|can\'t\s*bust", re.IGNORECASE),
    "Boss of the Road": re.compile(r"boss\s*(of\s*the\s*)?road", re.IGNORECASE),
    "Strong Reliable": re.compile(r"strong\s*reliable", re.IGNORECASE),
    "MW Pioneer": re.compile(r"mw.*pioneer|pioneer.*denim", re.IGNORECASE),
    "Blue Bell": re.compile(r"blue\s*bell", re.IGNORECASE),
    "Stronghold": re.compile(r"stronghold", re.IGNORECASE),
    "Crown": re.compile(r"\bcrown\b", re.IGNORECASE),
    "N&W": re.compile(r"\bn\s*&\s*w\b", re.IGNORECASE),
    "Black Bear": re.compile(r"black\s*bear", re.IGNORECASE),
    "Carter's": re.compile(r"carter", re.IGNORECASE),
    "Test": re.compile(r"\btest\b", re.IGNORECASE),
    "Powell": re.compile(r"powell", re.IGNORECASE),
    "W.P.A": re.compile(r"w\.?p\.?a\b", re.IGNORECASE),
    "Freeland": re.compile(r"freeland", re.IGNORECASE),
    "Burlington": re.compile(r"burlington", re.IGNORECASE),
    "Dubbleware": re.compile(r"dubbleware", re.IGNORECASE),
    "Winner": re.compile(r"\bwinner\b", re.IGNORECASE),
    "Round House": re.compile(r"round\s*house", re.IGNORECASE),
    "Pointer": re.compile(r"pointer", re.IGNORECASE),
    "Montgomery Ward": re.compile(r"montgomery\s*ward", re.IGNORECASE),
    "Sears/Roebucks": re.compile(r"sears|roebucks", re.IGNORECASE),
    "JCPenney/Foremost": re.compile(r"j\.?c\.?\s*penney|jcp|penneys|foremost", re.IGNORECASE),
    "Ox Hide": re.compile(r"ox\s*hide", re.IGNORECASE),
    
    # === WORKWEAR TYPES ===
    "Coverall": re.compile(r"coverall", re.IGNORECASE),
    "All-in-One": re.compile(r"all\s*in\s*one|all-in-one", re.IGNORECASE),
    "Overall (Bib)": re.compile(r"overall", re.IGNORECASE),
    "Painter Pants": re.compile(r"painter", re.IGNORECASE),
    "Double Knee": re.compile(r"double\s*knee", re.IGNORECASE),
    "Engineer Pants": re.compile(r"engineer", re.IGNORECASE),
    "Logger Pants": re.compile(r"logger", re.IGNORECASE),
    "Shop Coat": re.compile(r"shop\s*coat", re.IGNORECASE),
    "Chore Coat": re.compile(r"chore", re.IGNORECASE),
    "Chambray Shirt": re.compile(r"chambray", re.IGNORECASE),
    "Chinstrap": re.compile(r"chinstrap", re.IGNORECASE),
    "Buckleback": re.compile(r"buckleback", re.IGNORECASE),
    "Railroad": re.compile(r"railroad", re.IGNORECASE),
    "Prison": re.compile(r"prison", re.IGNORECASE),
    "Change Button": re.compile(r"change\s*button", re.IGNORECASE),
    "Denim Work Trousers": re.compile(r"denim.*(trouser|work\s*pant)|denim.*pant", re.IGNORECASE),
    "Work Shirt": re.compile(r"work\s*shirt", re.IGNORECASE),
    
    # === PATTERNS/MATERIALS ===
    "Star Stripe": re.compile(r"star\s*stripe", re.IGNORECASE),
    "Wabash Stripe": re.compile(r"wabash", re.IGNORECASE),
    "Hickory Stripe": re.compile(r"hickory", re.IGNORECASE),
    "Salt & Pepper": re.compile(r"salt.*pepper", re.IGNORECASE),
    "Duck Canvas": re.compile(r"duck\s*canvas|canvas\s*duck|brown\s*duck", re.IGNORECASE),
    "Blanket Lined": re.compile(r"blanket\s*lined", re.IGNORECASE),
    "Shadow Plaid": re.compile(r"shadow\s*plaid|shadow.*check", re.IGNORECASE),
    
    # === OUTDOOR BRANDS ===
    "Patagonia MARS": re.compile(r"patagonia.*mars|mars.*patagonia", re.IGNORECASE),
    "Patagonia DAS Parka": re.compile(r"patagonia.*das|das\s*parka", re.IGNORECASE),
    "Patagonia Glissade": re.compile(r"patagonia.*glissade|glissade", re.IGNORECASE),
    "Patagonia Snap-T": re.compile(r"patagonia.*snap-t|snap-t", re.IGNORECASE),
    "Patagonia Down": re.compile(r"patagonia.*(down|puff)", re.IGNORECASE),
    "Patagonia Retro": re.compile(r"patagonia.*retro", re.IGNORECASE),
    "Patagonia": re.compile(r"patagonia", re.IGNORECASE),
    "L.L.Bean Leather Handle Tote": re.compile(r"l\.?l\.?\s*bean.*leather.*handle|leather.*handle.*tote", re.IGNORECASE),
    "L.L.Bean Tote": re.compile(r"l\.?l\.?\s*bean.*tote", re.IGNORECASE),
    "L.L.Bean Warden": re.compile(r"l\.?l\.?\s*bean.*warden", re.IGNORECASE),
    "L.L.Bean": re.compile(r"l\.?l\.?\s*bean", re.IGNORECASE),
    "North Face": re.compile(r"north\s*face", re.IGNORECASE),
    "Eddie Bauer": re.compile(r"eddie\s*bauer", re.IGNORECASE),
    "REI": re.compile(r"\brei\b", re.IGNORECASE),
    "Brown's Beach": re.compile(r"brown'?s?\s*beach", re.IGNORECASE),
    "Rocky Mountain": re.compile(r"rocky\s*mountain", re.IGNORECASE),
    "Arc'teryx": re.compile(r"arc.teryx", re.IGNORECASE),
    "Filson": re.compile(r"filson", re.IGNORECASE),
    "Sierra Designs": re.compile(r"sierra\s*designs", re.IGNORECASE),
    "Alpine Designs": re.compile(r"alpine\s*designs", re.IGNORECASE),
    "Gerry": re.compile(r"\bgerry\b", re.IGNORECASE),
    "Barbour": re.compile(r"barbour", re.IGNORECASE),
    "Willis & Geiger": re.compile(r"willis.*geiger", re.IGNORECASE),
    "Down Jacket": re.compile(r"down\s*(jacket|parka|vest|hoody)|expedition.*down", re.IGNORECASE),
    "Hunting Jacket": re.compile(r"hunting.*jacket|shooting", re.IGNORECASE),
    "Ski Jacket": re.compile(r"ski\s*jacket", re.IGNORECASE),
    
    # === SOUVENIR/TOUR JACKETS ===
    "Vietnam Souvenir": re.compile(r"viet-?nam.*souvenir", re.IGNORECASE),
    "Vietnam Tour": re.compile(r"viet-?nam.*tour", re.IGNORECASE),
    "Japan Tiger Souvenir": re.compile(r"japan.*tiger|tiger.*souvenir", re.IGNORECASE),
    "Japan Corduroy Souvenir": re.compile(r"japan.*corduroy.*souvenir|corduroy.*souvenir", re.IGNORECASE),
    "Misawa Souvenir": re.compile(r"misawa", re.IGNORECASE),
    "Japan Souvenir": re.compile(r"japan.*souvenir|sukajan", re.IGNORECASE),
    "Okinawa Souvenir": re.compile(r"okinawa.*souvenir", re.IGNORECASE),
    "Korea Souvenir": re.compile(r"korea.*souvenir", re.IGNORECASE),
    "Alaska Souvenir": re.compile(r"alaska.*souvenir", re.IGNORECASE),
    "Souvenir Jacket Other": re.compile(r"souvenir", re.IGNORECASE),
    
    # === JACKET TYPES ===
    "Award/Car Club": re.compile(r"award\s*jacket|car\s*club", re.IGNORECASE),
    "Pharaoh Jacket": re.compile(r"pharaoh", re.IGNORECASE),
    "Drizzler Jacket": re.compile(r"drizzler", re.IGNORECASE),
    "Western Jacket": re.compile(r"western.*jacket|gabardine.*jacket", re.IGNORECASE),
    "Kodiak": re.compile(r"kodiak", re.IGNORECASE),
    "Fake Fur Jacket": re.compile(r"fake\s*fur", re.IGNORECASE),
    "Logger Cruiser": re.compile(r"logger.*cruiser|cruiser.*jacket", re.IGNORECASE),
    "Car Coat": re.compile(r"car\s*coat", re.IGNORECASE),
    "Mackinaw": re.compile(r"mackinaw", re.IGNORECASE),
    
    # === HAWAIIAN SHIRTS ===
    "Kamehameha": re.compile(r"kamehameha", re.IGNORECASE),
    "Kahanamoku": re.compile(r"kahanamoku", re.IGNORECASE),
    "Del Mar": re.compile(r"del\s*mar", re.IGNORECASE),
    "Hawaiian/Aloha": re.compile(r"hawaiian|aloha", re.IGNORECASE),
    
    # === SHIRTS ===
    "Rayon Shirt": re.compile(r"rayon.*shirt|rayon", re.IGNORECASE),
    "Pilgrim Flannel": re.compile(r"pilgrim.*flannel|pilgrim.*plaid|pilgrim", re.IGNORECASE),
    "Sun Valley Flannel": re.compile(r"sun\s*valley.*flannel|sun\s*valley", re.IGNORECASE),
    "Flannel Shirt": re.compile(r"flannel", re.IGNORECASE),
    "Western Shirt": re.compile(r"western.*shirt", re.IGNORECASE),
    "Wool Shirt": re.compile(r"wool.*shirt", re.IGNORECASE),
    "Denim Shirt": re.compile(r"denim.*shirt", re.IGNORECASE),
    "Corduroy Shirt": re.compile(r"corduroy.*shirt", re.IGNORECASE),
    "Arrow Shirt": re.compile(r"\barrow\b.*shirt|\barrow\b", re.IGNORECASE),
    
    # === TEES ===
    "Band Tee": re.compile(r"(nirvana|metallica|grateful\s*dead|rolling\s*stones|beatles|zeppelin|hendrix|floyd|ramones|clash|pistols|sabbath|maiden|motorhead|slayer|megadeth|pantera|soundgarden|pearl\s*jam|alice\s*in\s*chains|dinosaur|red\s*hot|radio\s*head|guns.*roses|ac\/dc|kiss\b|van\s*halen|def\s*leppard|ozzy|judas\s*priest|iron\s*maiden).*t-shirt|tour.*t-shirt", re.IGNORECASE),
    "Harley-Davidson Tee": re.compile(r"harley.*t-shirt|harley.*tee", re.IGNORECASE),
    "Stussy": re.compile(r"stussy", re.IGNORECASE),
    "Photo Print Tee": re.compile(r"photo.*print|bruce\s*weber", re.IGNORECASE),
    "Vintage T-Shirt": re.compile(r"t-shirt", re.IGNORECASE),
    
    # === SWEATERS ===
    "Mohair Cardigan": re.compile(r"mohair", re.IGNORECASE),
    "Cowichan Jacket": re.compile(r"cowichan.*jacket", re.IGNORECASE),
    "Cowichan Sweater": re.compile(r"cowichan", re.IGNORECASE),
    "Pendleton": re.compile(r"pendleton", re.IGNORECASE),
    
    # === NATIVE/SOUTHWEST ===
    "Navajo/Chimayo": re.compile(r"navajo|chimayo|ortega", re.IGNORECASE),
    
    # === LEATHER ===
    "Buco Leather": re.compile(r"\bbuco\b", re.IGNORECASE),
    "Schott Leather": re.compile(r"schott", re.IGNORECASE),
    "Beck Leather": re.compile(r"\bbeck\b", re.IGNORECASE),
    "Horsehide": re.compile(r"horsehide", re.IGNORECASE),
    
    # === PREMIUM BRANDS ===
    "Brooks Brothers": re.compile(r"brooks\s*brothers", re.IGNORECASE),
    "McGregor": re.compile(r"mcgregor", re.IGNORECASE),
    "Burberry": re.compile(r"burberry", re.IGNORECASE),
    
    # === COLLEGE/SPORTS ===
    "Beer Jacket": re.compile(r"beer\s*jacket", re.IGNORECASE),
    "Varsity/Letterman": re.compile(r"varsity|letterman", re.IGNORECASE),
    "Sports Jacket": re.compile(r"sports?\s*jacket", re.IGNORECASE),
    "Military Academy": re.compile(r"usafa|usma|usna|west\s*point|naval\s*academy|air\s*force\s*academy", re.IGNORECASE),
    
    # === SHOES ===
    "Nike Shoes": re.compile(r"nike.*(shoes|sneaker|jordan|dunk|cortez|waffle|blazer)", re.IGNORECASE),
    "Nike": re.compile(r"\bnike\b", re.IGNORECASE),
    "Converse": re.compile(r"converse", re.IGNORECASE),
    "Red Wing Boots": re.compile(r"red\s*wing", re.IGNORECASE),
    "Vans": re.compile(r"\bvans\b", re.IGNORECASE),
    
    # === ACCESSORIES ===
    "Advertising/Display": re.compile(r"advertising|banner|display|sign\b|wall\s*clock", re.IGNORECASE),
    "Tote Bag": re.compile(r"tote", re.IGNORECASE),
    "Quilt": re.compile(r"quilt", re.IGNORECASE),
    "Navajo Rug": re.compile(r"navajo.*rug|swastika.*rug|\brug\b", re.IGNORECASE),
    "Canvas Bag": re.compile(r"canvas.*bag", re.IGNORECASE),
    "Newspaper Bag": re.compile(r"newspaper", re.IGNORECASE),
    "Backpack": re.compile(r"back\s*pack|backpack|gregory|chouinard|kelty", re.IGNORECASE),
    "Studded Belt": re.compile(r"studded.*belt|jewel.*belt", re.IGNORECASE),
    "Belt": re.compile(r"\bbelt\b", re.IGNORECASE),
    "Blanket": re.compile(r"\bblanket\b", re.IGNORECASE),
    "Apron": re.compile(r"\bapron\b", re.IGNORECASE),
    "Bandana": re.compile(r"bandana", re.IGNORECASE),
    "Indigo": re.compile(r"\bindigo\b", re.IGNORECASE),
    
    # === KNITS/SWEATERS ===
    "Cardigan": re.compile(r"cardigan", re.IGNORECASE),
    "Vest": re.compile(r"\bvest\b", re.IGNORECASE),
    "Pullover Sweater": re.compile(r"pullover", re.IGNORECASE),
    
    # === CATCH-ALLS ===
    "Cafe Racer": re.compile(r"cafe\s*racer", re.IGNORECASE),
    "Motorcycle Jacket": re.compile(r"motorcycle", re.IGNORECASE),
    "Denim Jacket": re.compile(r"denim\s*jacket", re.IGNORECASE),
    "Denim Jeans": re.compile(r"jeans", re.IGNORECASE),
    "Gore-Tex": re.compile(r"gore-tex", re.IGNORECASE),
    "Corduroy": re.compile(r"corduroy", re.IGNORECASE),
    "Polo/Ralph Lauren": re.compile(r"polo|ralph\s*lauren", re.IGNORECASE),
    "Anorak": re.compile(r"anorak", re.IGNORECASE),
    "Leather Jacket": re.compile(r"leather.*jacket", re.IGNORECASE),
    "Work Jacket": re.compile(r"work\s*jacket", re.IGNORECASE),
    "Boat Jacket": re.compile(r"boat\s*jacket", re.IGNORECASE),
    "Parka": re.compile(r"parka", re.IGNORECASE),
    
    # === ERA CATCH-ALLS (after all brands) ===
    "WW1 Military": re.compile(r"ww1", re.IGNORECASE),
    "WW2 Military": re.compile(r"ww2", re.IGNORECASE),
    
    # === FINAL CATCH-ALLS ===
    "Jacket (Other)": re.compile(r"jacket", re.IGNORECASE),
    "Shirt (Other)": re.compile(r"shirt", re.IGNORECASE),
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
