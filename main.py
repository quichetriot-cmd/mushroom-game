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
import re
import json
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict
from collections import defaultdict
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


# ========== TRENDS ANALYSIS ==========
"""
Complete category patterns for trends analysis.
Replace the CATEGORIES dict in the backend code with this.
"""

import re

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
    "Can't Bust'Em": re.compile(r"can't\s*bust", re.IGNORECASE),
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
    
    # === WORKWEAR TYPES ===
    "Coverall": re.compile(r"coverall", re.IGNORECASE),
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
    
    # === PATTERNS/MATERIALS ===
    "Star Stripe": re.compile(r"star\s*stripe", re.IGNORECASE),
    "Wabash Stripe": re.compile(r"wabash", re.IGNORECASE),
    "Hickory Stripe": re.compile(r"hickory", re.IGNORECASE),
    "Salt & Pepper": re.compile(r"salt.*pepper", re.IGNORECASE),
    "Duck Canvas": re.compile(r"duck\s*canvas|canvas\s*duck|brown\s*duck", re.IGNORECASE),
    "Blanket Lined": re.compile(r"blanket\s*lined", re.IGNORECASE),
    
    # === OUTDOOR BRANDS ===
    "Patagonia": re.compile(r"patagonia", re.IGNORECASE),
    "L.L.Bean Tote": re.compile(r"l\.?l\.?\s*bean.*tote", re.IGNORECASE),
    "L.L.Bean Warden": re.compile(r"l\.?l\.?\s*bean.*warden", re.IGNORECASE),
    "L.L.Bean Other": re.compile(r"l\.?l\.?\s*bean", re.IGNORECASE),
    "North Face": re.compile(r"north\s*face", re.IGNORECASE),
    "Eddie Bauer": re.compile(r"eddie\s*bauer", re.IGNORECASE),
    "REI": re.compile(r"\brei\b", re.IGNORECASE),
    "Brown's Beach": re.compile(r"brown.*beach", re.IGNORECASE),
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
    
    # === HAWAIIAN SHIRTS ===
    "Kamehameha": re.compile(r"kamehameha", re.IGNORECASE),
    "Kahanamoku": re.compile(r"kahanamoku", re.IGNORECASE),
    "Del Mar": re.compile(r"del\s*mar", re.IGNORECASE),
    "Hawaiian/Aloha": re.compile(r"hawaiian|aloha", re.IGNORECASE),
    
    # === SHIRTS ===
    "Rayon Shirt": re.compile(r"rayon", re.IGNORECASE),
    "Pilgrim Flannel": re.compile(r"pilgrim.*flannel|pilgrim.*plaid", re.IGNORECASE),
    "Sun Valley Flannel": re.compile(r"sun\s*valley.*flannel", re.IGNORECASE),
    "Flannel Shirt": re.compile(r"flannel", re.IGNORECASE),
    "Western Shirt": re.compile(r"western.*shirt", re.IGNORECASE),
    "Wool Shirt": re.compile(r"wool.*shirt", re.IGNORECASE),
    "Work Shirt": re.compile(r"work.*shirt", re.IGNORECASE),
    "Denim Shirt": re.compile(r"denim.*shirt", re.IGNORECASE),
    "Corduroy Shirt": re.compile(r"corduroy.*shirt", re.IGNORECASE),
    "Arrow Shirt": re.compile(r"arrow", re.IGNORECASE),
    
    # === TEES ===
    "Band/Tour Tee": re.compile(r"tour.*t-shirt|t-shirt.*tour", re.IGNORECASE),
    "Photo Print Tee": re.compile(r"photo.*print|bruce\s*weber", re.IGNORECASE),
    "Vintage T-Shirt": re.compile(r"t-shirt", re.IGNORECASE),
    
    # === SWEATERS ===
    "Mohair Cardigan": re.compile(r"mohair", re.IGNORECASE),
    "Cowichan Jacket": re.compile(r"cowichan.*jacket", re.IGNORECASE),
    "Cowichan Sweater": re.compile(r"cowichan", re.IGNORECASE),
    "Pendleton": re.compile(r"pendleton", re.IGNORECASE),
    "Mackinaw": re.compile(r"mackinaw", re.IGNORECASE),
    
    # === NATIVE/SOUTHWEST ===
    "Navajo/Chimayo": re.compile(r"navajo|chimayo", re.IGNORECASE),
    
    # === LEATHER ===
    "Buco Leather": re.compile(r"buco", re.IGNORECASE),
    "Schott Leather": re.compile(r"schott", re.IGNORECASE),
    "Beck Leather": re.compile(r"\bbeck\b", re.IGNORECASE),
    "Horsehide": re.compile(r"horsehide", re.IGNORECASE),
    "Car Coat": re.compile(r"car\s*coat", re.IGNORECASE),
    
    # === PREMIUM BRANDS ===
    "Brooks Brothers": re.compile(r"brooks\s*brothers", re.IGNORECASE),
    "McGregor": re.compile(r"mcgregor", re.IGNORECASE),
    
    # === COLLEGE/SPORTS ===
    "Beer Jacket": re.compile(r"beer\s*jacket", re.IGNORECASE),
    "Varsity/Letterman": re.compile(r"varsity|letterman", re.IGNORECASE),
    "Sports Jacket": re.compile(r"sports?\s*jacket", re.IGNORECASE),
    "Flock Print": re.compile(r"flock\s*print", re.IGNORECASE),
    "Military Academy": re.compile(r"usafa|usma|usna|west\s*point|naval\s*academy|air\s*force\s*academy", re.IGNORECASE),
    
    # === SHOES ===
    "Converse": re.compile(r"converse", re.IGNORECASE),
    
    # === ACCESSORIES ===
    "Advertising/Display": re.compile(r"advertising|banner|display|sign\b", re.IGNORECASE),
    "Tote Bag": re.compile(r"tote", re.IGNORECASE),
    "Quilt": re.compile(r"quilt", re.IGNORECASE),
    
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
    """Calculate market trends with optimized queries."""
    global _trends_cache
    
    # Check cache
    now = datetime.now()
    if _trends_cache["data"] and _trends_cache["timestamp"]:
        age = (now - _trends_cache["timestamp"]).total_seconds()
        if age < CACHE_DURATION:
            return _trends_cache["data"]
    
    # Date ranges
    this_month_start = datetime(now.year, now.month, 1).date()
    six_months_ago = (now - timedelta(days=180)).date()
    
    # Fetch items in one query
    items = db.query(Item).filter(Item.sold_date >= six_months_ago).all()
    
    # Categorize all items
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
    
    results.sort(key=lambda x: x["price_change"], reverse=True)
    
    # Generate summary
    hot = [r for r in results if r["price_change"] > 20 and r["recent_count"] >= 2]
    cold = [r for r in results if r["price_change"] < -20 and r["recent_count"] >= 2]
    volume_up = [r for r in results if r["volume_change"] > 50 and r["recent_count"] >= 2]
    volume_down = [r for r in results if r["recent_count"] == 0 and r["trailing_monthly_avg"] >= 1]
    
    summary_parts = []
    if hot:
        top_hot = ", ".join([f"{r['category']} (+{r['price_change']}%)" for r in hot[:2]])
        summary_parts.append(f"<strong>Prices up:</strong> {top_hot}")
    if cold:
        top_cold = ", ".join([f"{r['category']} ({r['price_change']}%)" for r in cold[:2]])
        summary_parts.append(f"<strong>Prices down:</strong> {top_cold}")
    if volume_down:
        not_listed = ", ".join([r["category"] for r in volume_down[:3]])
        summary_parts.append(f"<strong>Not listing this month:</strong> {not_listed}")
    if volume_up:
        pushing = ", ".join([r["category"] for r in volume_up[:2]])
        summary_parts.append(f"<strong>Pushing more:</strong> {pushing}")
    
    trailing_monthly = round(len(trailing) / 6)
    summary_parts.append(f"<br><br><strong>This month:</strong> {len(this_month)} items (avg {trailing_monthly}/mo)")
    
    if not any([hot, cold, volume_down, volume_up]):
        summary_parts.insert(0, "Market appears stable this month. No major shifts detected.")
    
    summary = ". ".join(summary_parts) + "."
    
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


# ========== API ROUTES ==========

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
    exclude: Optional[str] = None,
    db: Session = Depends(get_db)
):
    from sqlalchemy.sql.expression import func
    
    count = min(count, 100)
    query = db.query(Item)
    
    if min_year:
        query = query.filter(Item.sold_date >= date(min_year, 1, 1))
    
    if exclude:
        try:
            exclude_ids = [int(x.strip()) for x in exclude.split(",") if x.strip()]
            if exclude_ids:
                query = query.filter(~Item.id.in_(exclude_ids))
        except ValueError:
            pass
    
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


@app.get("/api/trends")
def get_trends(db: Session = Depends(get_db)):
    """Get market trends analysis (cached for 1 hour)"""
    return calculate_trends(db)


@app.get("/api/trends/test")
def test_category(title: str):
    """Test what category a title matches"""
    category = categorize_item(title)
    
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


@app.post("/api/trends/clear-cache")
def clear_trends_cache(username: str = Depends(verify_credentials)):
    """Clear trends cache (requires auth)"""
    global _trends_cache
    _trends_cache = {"data": None, "timestamp": None}
    return {"message": "Cache cleared"}


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
