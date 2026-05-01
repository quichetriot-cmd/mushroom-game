import requests
from bs4 import BeautifulSoup
import json
import re
import time
import logging
from datetime import datetime
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ── Mushroom config ───────────────────────────────────────────
BASE_URL = "https://vintage-mushroom.net"
CATEGORY_URL = f"{BASE_URL}/?mode=cate&csid=0&cbid=1809250&sort=n"
YEN_TO_USD = 150
MIN_PRICE_YEN = 65000
MAX_CONSECUTIVE_EXISTING = 5

# ── Something Happens config ──────────────────────────────────
SH_BASE_URL = "https://www.somethinghappens-dressing.com"
SH_MIN_PRICE_USD = 300

# ── Acorn config ───────────────────────────────────────────────
ACORN_BASE_URL = "https://acorn-onlinestore.com"
ACORN_COLLECTION_URL = f"{ACORN_BASE_URL}/collections/all/products.json"
ACORN_PAGE_SIZE = 100
ACORN_PAGE_DELAY_SECONDS = 2
ACORN_MAX_RETRIES = 5

session = requests.Session()
retry = Retry(
    total=5,
    backoff_factor=2,
    status_forcelist=[500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)

session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
})


# ── Shared helpers ────────────────────────────────────────────

def clean_text(text: str) -> str:
    if not text:
        return text
    text = text.replace('\\', '')
    text = text.replace('\u3000', ' ')
    text = text.replace('\xa0', ' ')
    text = text.replace('\r', ' ')
    text = text.replace('\n', ' ')
    text = text.replace('\t', ' ')
    while '  ' in text:
        text = text.replace('  ', ' ')
    return text.strip()


def translate_text(text: str) -> str:
    if not text or len(text.strip()) == 0:
        return text
    try:
        from deep_translator import GoogleTranslator
        if len(text) > 4500:
            text = text[:4500]
        result = GoogleTranslator(source='ja', target='en').translate(text)
        return result if result else text
    except Exception as e:
        logging.warning(f"Translation failed: {e}")
        return text


def parse_iso_datetime(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=None)
    except Exception:
        return None


def item_exists(db, store: str, title: str) -> bool:
    from models import Item
    existing = db.query(Item).filter(
        Item.store == store,
        Item.title == title
    ).first()
    return existing is not None


def add_item_to_db(db, item_data: dict) -> bool:
    from models import Item
    try:
        sold_date = datetime.strptime(item_data['sold_date'], '%Y-%m-%d').date()
        item = Item(
            store=item_data.get('store', 'mushroom'),
            title=item_data['title'],
            price_yen=item_data['price_yen'],
            price_usd=item_data['price_usd'],
            description=item_data['description'],
            images=json.dumps(item_data['images']),
            sold_date=sold_date
        )
        db.add(item)
        db.commit()
        return True
    except Exception as e:
        logging.error(f"Error adding item to DB: {e}")
        db.rollback()
        return False


def get_item_count(db) -> int:
    from models import Item
    return db.query(Item).count()


def build_tag_description(tags: list[str]) -> str:
    cleaned_tags = []
    seen = set()
    for tag in tags:
        normalized = clean_text(tag)
        if not normalized:
            continue
        if re.fullmatch(r"20\d{6}", normalized):
            continue
        if normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        cleaned_tags.append(normalized)
        if len(cleaned_tags) >= 16:
            break
    return " / ".join(cleaned_tags)


# ── Mushroom scraper ──────────────────────────────────────────

def parse_timestamp(url: str) -> Optional[str]:
    if "cmsp_timestamp=" not in url:
        return None
    try:
        raw_timestamp = url.split("cmsp_timestamp=")[-1]
        raw_timestamp = raw_timestamp.split("&")[0]
        if len(raw_timestamp) >= 8:
            year = raw_timestamp[:4]
            month = raw_timestamp[4:6]
            day = raw_timestamp[6:8]
            datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
            return f"{year}-{month}-{day}"
    except:
        pass
    return None


def scrape_product_page(product_url: str) -> Optional[dict]:
    try:
        response = session.get(product_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        title_tag = soup.find('meta', property='og:title')
        if not title_tag or not title_tag.get('content'):
            return None

        title = title_tag['content'].strip()
        title = title.replace(' - 古着屋 ｜ mushroom(マッシュルーム)\u3000ヴィンテージクロージングストア', '').strip()

        if not title:
            return None

        description = ''
        description_tag = soup.find('div', class_='product_exp')
        if description_tag:
            description = description_tag.get_text(strip=True)
            description = ' '.join(description.replace('\u3000', ' ').split())
            if description:
                description = translate_text(description)

        price = None
        for script in soup.find_all('script'):
            if script.string and 'sales_price_including_tax' in script.string:
                try:
                    script_text = script.string
                    start = script_text.find('"sales_price_including_tax":') + len('"sales_price_including_tax":')
                    end = script_text.find(',', start)
                    price_str = script_text[start:end].strip().strip('"').replace(',', '')
                    price = int(price_str)
                    break
                except:
                    continue

        if not price or price < MIN_PRICE_YEN:
            return None

        images = []
        sold_date = None

        img_tags = []
        for selector in ['.product_image_thumb img', '.product_thumb img']:
            img_tags = soup.select(selector)
            if img_tags:
                break

        if not img_tags:
            main_img = soup.select_one('.product_image img')
            if main_img:
                img_tags = [main_img]

        for img in img_tags[:10]:
            img_src = img.get('src', '')
            if not img_src:
                continue
            if img_src.startswith('http'):
                img_url = img_src
            elif img_src.startswith('//'):
                img_url = 'https:' + img_src
            else:
                img_url = f"{BASE_URL}/{img_src.lstrip('/')}"
            images.append(img_url)
            if not sold_date:
                sold_date = parse_timestamp(img_url)

        if not images:
            return None

        if not sold_date:
            sold_date = datetime.now().strftime("%Y-%m-%d")

        return {
            'store': 'mushroom',
            'title': clean_text(title),
            'price_yen': price,
            'price_usd': round(price / YEN_TO_USD),
            'description': clean_text(description),
            'images': images,
            'sold_date': sold_date
        }

    except Exception as e:
        logging.error(f"Error scraping {product_url}: {e}")
        return None


def get_product_links(page_num: int) -> Optional[list]:
    page_url = f"{CATEGORY_URL}&page={page_num}"
    try:
        response = session.get(page_url, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        links = []
        for selector in ['.prd_lst_unit .prd_lst_link', '.product_list_unit a']:
            items = soup.select(selector)
            if items:
                for item in items:
                    href = item.get('href', '')
                    if href:
                        if href.startswith('http'):
                            links.append(href)
                        else:
                            links.append(BASE_URL + href)
                break

        return links if links else None

    except Exception as e:
        logging.error(f"Error fetching page {page_num}: {e}")
        return None


def run_incremental_scrape(db, max_pages: int = 50) -> int:
    new_items = 0
    consecutive_existing = 0

    logging.info("="*50)
    logging.info("MUSHROOM INCREMENTAL SCRAPE - Checking for new items...")
    logging.info("="*50)

    for page in range(1, max_pages + 1):
        logging.info(f"Page {page}...")

        links = get_product_links(page)
        if not links:
            logging.info("No more pages found.")
            break

        for link in links:
            item_data = scrape_product_page(link)

            if not item_data:
                continue

            if item_exists(db, item_data['store'], item_data['title']):
                consecutive_existing += 1
                logging.info(f"  EXISTING ({consecutive_existing}/{MAX_CONSECUTIVE_EXISTING}): {item_data['title'][:40]}...")

                if consecutive_existing >= MAX_CONSECUTIVE_EXISTING:
                    logging.info(f"Hit {MAX_CONSECUTIVE_EXISTING} existing items. Stopping.")
                    logging.info(f"Added {new_items} new items this run.")
                    return new_items
            else:
                consecutive_existing = 0
                if add_item_to_db(db, item_data):
                    new_items += 1
                    logging.info(f"  NEW: ${item_data['price_usd']:,} - {item_data['title'][:45]}...")

            time.sleep(0.5)

        time.sleep(1)

    logging.info(f"Scrape complete. Added {new_items} new items.")
    return new_items


def run_full_scrape(db, max_pages: int = 999) -> int:
    new_items = 0
    skipped = 0

    logging.info("="*50)
    logging.info("MUSHROOM FULL SCRAPE - Getting all items...")
    logging.info("="*50)

    for page in range(1, max_pages + 1):
        logging.info(f"Page {page}...")

        links = get_product_links(page)
        if not links:
            logging.info(f"No more pages. Stopped at page {page}.")
            break

        for link in links:
            item_data = scrape_product_page(link)

            if not item_data:
                continue

            if item_exists(db, item_data['store'], item_data['title']):
                skipped += 1
            else:
                if add_item_to_db(db, item_data):
                    new_items += 1
                    logging.info(f"  ${item_data['price_usd']:,} - {item_data['title'][:50]}...")

            time.sleep(0.5)

        logging.info(f"Page {page} done. Total: {new_items} new, {skipped} skipped")
        time.sleep(1)

    logging.info("="*50)
    logging.info(f"MUSHROOM FULL SCRAPE COMPLETE")
    logging.info(f"  New items: {new_items}")
    logging.info(f"  Skipped (duplicates): {skipped}")
    logging.info(f"  Total in database: {get_item_count(db)}")
    logging.info("="*50)

    return new_items


def run_smart_scrape(db) -> int:
    from models import Item
    count = db.query(Item).filter(Item.store == 'mushroom').count()
    if count == 0:
        logging.info("No mushroom items. Running full scrape...")
        return run_full_scrape(db)
    else:
        logging.info(f"Database has {count} mushroom items. Running incremental scrape...")
        return run_incremental_scrape(db)


# ── Something Happens scraper ─────────────────────────────────

def sh_parse_product(product: dict) -> Optional[dict]:
    try:
        title = product.get('title', '').strip()
        if not title:
            return None

        variants = product.get('variants', [])
        if not variants:
            return None

        # Only want sold items
        if variants[0].get('available', True):
            return None

        price_usd = float(variants[0].get('price', 0))
        if price_usd < SH_MIN_PRICE_USD:
            return None

        images = [img['src'] for img in product.get('images', [])[:10]]
        if not images:
            return None

        # Strip HTML from description then translate
        body_html = product.get('body_html', '') or ''
        soup = BeautifulSoup(body_html, 'html.parser')
        description = soup.get_text(separator=' ', strip=True)
        description = clean_text(description)
        if description:
            description = translate_text(description)

        published_at = product.get('published_at', '')
        try:
            sold_date = datetime.fromisoformat(published_at).strftime('%Y-%m-%d')
        except:
            sold_date = datetime.now().strftime('%Y-%m-%d')

        return {
            'store': 'somethinghappens',
            'title': clean_text(title),
            'price_yen': round(price_usd * YEN_TO_USD),
            'price_usd': round(price_usd),
            'description': description,
            'images': images,
            'sold_date': sold_date,
        }

    except Exception as e:
        logging.error(f"Error parsing SH product: {e}")
        return None


def run_sh_scrape(db) -> int:
    new_items = 0
    skipped = 0
    page = 1

    logging.info("="*50)
    logging.info("SOMETHING HAPPENS SCRAPE - Getting all sold items...")
    logging.info("="*50)

    while True:
        url = f"{SH_BASE_URL}/collections/sold/products.json?limit=250&page={page}"
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            products = response.json().get('products', [])
        except Exception as e:
            logging.error(f"Error fetching SH page {page}: {e}")
            break

        if not products:
            break

        logging.info(f"Page {page} — {len(products)} products")

        for product in products:
            item_data = sh_parse_product(product)
            if not item_data:
                continue

            if item_exists(db, item_data['store'], item_data['title']):
                skipped += 1
            else:
                if add_item_to_db(db, item_data):
                    new_items += 1
                    logging.info(f"  NEW: ${item_data['price_usd']:,} - {item_data['title'][:50]}...")

            time.sleep(0.3)

        page += 1
        time.sleep(1)

    logging.info("="*50)
    logging.info(f"SOMETHING HAPPENS SCRAPE COMPLETE")
    logging.info(f"  New items: {new_items}")
    logging.info(f"  Skipped (duplicates): {skipped}")
    logging.info("="*50)

    return new_items


# ── Acorn scraper ──────────────────────────────────────────────

def fetch_acorn_products_page(page: int) -> list[dict]:
    params = {
        "limit": ACORN_PAGE_SIZE,
        "page": page,
    }

    for attempt in range(1, ACORN_MAX_RETRIES + 1):
        try:
            response = session.get(ACORN_COLLECTION_URL, params=params, timeout=30)
            if response.status_code == 429:
                wait_seconds = min(60, 5 * attempt)
                logging.warning(f"Acorn rate limited on page {page}. Waiting {wait_seconds}s before retry {attempt}/{ACORN_MAX_RETRIES}.")
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            return response.json().get("products", [])
        except Exception as e:
            if attempt == ACORN_MAX_RETRIES:
                raise RuntimeError(f"Acorn page {page} failed after {ACORN_MAX_RETRIES} attempts: {e}") from e
            wait_seconds = min(60, 3 * attempt)
            logging.warning(f"Acorn page {page} request failed ({e}). Waiting {wait_seconds}s before retry {attempt}/{ACORN_MAX_RETRIES}.")
            time.sleep(wait_seconds)

    return []


def parse_acorn_product(product: dict) -> Optional[dict]:
    try:
        variants = product.get("variants", [])
        if not variants:
            return None

        primary_variant = variants[0]
        title = clean_text(product.get("title", ""))
        handle = clean_text(product.get("handle", ""))
        if not title or not handle:
            return None

        price_yen = int(primary_variant.get("price") or 0)
        tags = product.get("tags", []) or []
        body_html = product.get("body_html") or ""
        description = clean_text(BeautifulSoup(body_html, "html.parser").get_text(separator=" ", strip=True))
        if not description:
            description = build_tag_description(tags)

        return {
            "store": "acorn",
            "external_id": str(product.get("id", "")),
            "handle": handle,
            "title": title,
            "price_yen": price_yen,
            "price_usd": round(price_yen / YEN_TO_USD) if price_yen else 0,
            "description": description,
            "images": [img.get("src") for img in product.get("images", [])[:10] if img.get("src")],
            "tags": tags,
            "is_available": bool(primary_variant.get("available", True)),
            "created_at": parse_iso_datetime(product.get("created_at")),
            "published_at": parse_iso_datetime(product.get("published_at")),
            "updated_at": parse_iso_datetime(product.get("updated_at")),
        }
    except Exception as e:
        logging.error(f"Error parsing Acorn product: {e}")
        return None


def upsert_tracked_product(db, product_data: dict):
    from models import TrackedProduct

    tracked = db.query(TrackedProduct).filter(
        TrackedProduct.store == product_data["store"],
        TrackedProduct.handle == product_data["handle"],
    ).first()

    if tracked is None:
        tracked = TrackedProduct(
            store=product_data["store"],
            external_id=product_data["external_id"],
            handle=product_data["handle"],
            title=product_data["title"],
            price_yen=product_data["price_yen"],
            price_usd=product_data["price_usd"],
            is_available=product_data["is_available"],
            description=product_data["description"],
            tags=json.dumps(product_data["tags"]),
            images=json.dumps(product_data["images"]),
            created_at=product_data["created_at"],
            published_at=product_data["published_at"],
            updated_at=product_data["updated_at"],
            last_seen_at=datetime.utcnow(),
        )
        if not product_data["is_available"]:
            tracked.sold_detected_at = datetime.utcnow()
        db.add(tracked)
        db.flush()
        return tracked, True

    was_available = tracked.is_available
    tracked.external_id = product_data["external_id"]
    tracked.title = product_data["title"]
    tracked.price_yen = product_data["price_yen"]
    tracked.price_usd = product_data["price_usd"]
    tracked.is_available = product_data["is_available"]
    tracked.description = product_data["description"]
    tracked.tags = json.dumps(product_data["tags"])
    tracked.images = json.dumps(product_data["images"])
    tracked.created_at = product_data["created_at"]
    tracked.published_at = product_data["published_at"]
    tracked.updated_at = product_data["updated_at"]
    tracked.last_seen_at = datetime.utcnow()

    if was_available and not tracked.is_available and tracked.sold_detected_at is None:
        tracked.sold_detected_at = datetime.utcnow()

    return tracked, False


def export_acorn_sold_item(db, tracked_product) -> bool:
    from models import Item

    sold_date_dt = tracked_product.published_at or tracked_product.created_at or tracked_product.sold_detected_at or datetime.utcnow()
    sold_date = sold_date_dt.strftime("%Y-%m-%d")
    item_data = {
        "store": "acorn",
        "title": tracked_product.title,
        "price_yen": tracked_product.price_yen or 0,
        "price_usd": tracked_product.price_usd or 0,
        "description": tracked_product.description or "",
        "images": tracked_product.get_images(),
        "sold_date": sold_date,
    }

    if tracked_product.exported_item_id:
        item = db.query(Item).filter(Item.id == tracked_product.exported_item_id).first()
        if item:
            item.title = item_data["title"]
            item.price_yen = item_data["price_yen"]
            item.price_usd = item_data["price_usd"]
            item.description = item_data["description"]
            item.images = json.dumps(item_data["images"])
            item.sold_date = datetime.strptime(item_data["sold_date"], "%Y-%m-%d").date()
            db.flush()
            return False

    existing_item = db.query(Item).filter(
        Item.store == "acorn",
        Item.title == tracked_product.title,
    ).first()
    if existing_item:
        tracked_product.exported_item_id = existing_item.id
        existing_item.price_yen = item_data["price_yen"]
        existing_item.price_usd = item_data["price_usd"]
        existing_item.description = item_data["description"]
        existing_item.images = json.dumps(item_data["images"])
        existing_item.sold_date = datetime.strptime(item_data["sold_date"], "%Y-%m-%d").date()
        db.flush()
        return False

    item = Item(
        store=item_data["store"],
        title=item_data["title"],
        price_yen=item_data["price_yen"],
        price_usd=item_data["price_usd"],
        description=item_data["description"],
        images=json.dumps(item_data["images"]),
        sold_date=datetime.strptime(item_data["sold_date"], "%Y-%m-%d").date(),
    )
    db.add(item)
    db.flush()
    tracked_product.exported_item_id = item.id
    return True


def run_acorn_scrape(db) -> int:
    from models import TrackedProduct

    logging.info("=" * 50)
    logging.info("ACORN SYNC - Tracking all products, exporting sold items only...")
    logging.info("=" * 50)

    page = 1
    new_tracked = 0
    exported_sold = 0
    updated_tracked = 0

    while True:
        try:
            products = fetch_acorn_products_page(page)
        except Exception as e:
            logging.error(f"Acorn sync stopped on page {page}: {e}")
            break

        if not products:
            logging.info(f"Acorn returned no products on page {page}.")
            break

        logging.info(f"Acorn page {page} — {len(products)} products")

        for product in products:
            try:
                product_data = parse_acorn_product(product)
                if not product_data:
                    continue

                tracked_product, is_new = upsert_tracked_product(db, product_data)
                if is_new:
                    new_tracked += 1
                else:
                    updated_tracked += 1

                if not tracked_product.is_available:
                    if export_acorn_sold_item(db, tracked_product):
                        exported_sold += 1

                db.commit()
            except Exception as e:
                db.rollback()
                logging.error(f"Acorn product sync failed for {product.get('handle')}: {e}")

        page += 1
        time.sleep(ACORN_PAGE_DELAY_SECONDS)

    total_tracked = db.query(TrackedProduct).filter(TrackedProduct.store == "acorn").count()
    logging.info("=" * 50)
    logging.info("ACORN SYNC COMPLETE")
    logging.info(f"  New tracked products: {new_tracked}")
    logging.info(f"  Updated tracked products: {updated_tracked}")
    logging.info(f"  Newly exported sold items: {exported_sold}")
    logging.info(f"  Total tracked products: {total_tracked}")
    logging.info("=" * 50)

    return exported_sold

# ── BerBerJin config ───────────────────────────────────────────
BBJ_BASE_URL = "https://webstore.berberjin.com"
BBJ_SOLD_URL = f"{BBJ_BASE_URL}/view/category/sold"
BBJ_MIN_PRICE_YEN = 28000
BBJ_PAGE_DELAY_SECONDS = 2
BBJ_ITEM_DELAY_SECONDS = 1


def make_bbj_session():
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1.5,
                                            status_forcelist=[429, 500, 502, 503]))
    s.mount("https://", adapter)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Referer": BBJ_BASE_URL,
    })
    return s


def fetch_bbj_category_page(s, page: int) -> list[str]:
    """Return list of item URLs from one sold category page."""
    try:
        r = s.get(BBJ_SOLD_URL, params={"sort": "order", "page": page}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logging.warning(f"BBJ category page {page} fetch failed: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    urls = []
    for a in soup.select("ul.item-list a[href*='/view/item/']"):
        href = a["href"]
        if href.startswith("/"):
            href = BBJ_BASE_URL + href
        # strip category_page_id param — we'll confirm sold on item page
        href = href.split("?")[0]
        if href not in urls:
            urls.append(href)
    return urls


def parse_bbj_item_page(s, url: str) -> Optional[dict]:
    """Scrape a single BBJ item page. Returns None if not sold, ¥0, or below min price."""
    try:
        r = s.get(url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logging.warning(f"BBJ item fetch failed {url}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # ── Confirm sold ──────────────────────────────────────────
    sold_btn = soup.select_one("div.sell-period-btn p")
    if not sold_btn or sold_btn.get_text(strip=True).lower() != "sold out":
        return None  # still available

    # ── Price ─────────────────────────────────────────────────
    price_el = soup.select_one("span.dtl-price-num")
    if not price_el:
        return None
    price_str = price_el.get_text(strip=True).replace(",", "")
    try:
        price_tax_inc = int(price_str)
    except ValueError:
        return None
    if price_tax_inc == 0:
        return None
    # BBJ prices are tax-inclusive (×1.1); convert to pre-tax yen
    price_yen = round(price_tax_inc / 1.1)
    if price_yen < BBJ_MIN_PRICE_YEN:
        return None

    # ── Title ─────────────────────────────────────────────────
    title_el = soup.select_one("section.contents-area h2")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

    # ── Images ────────────────────────────────────────────────
    images = []
    for img in soup.select("ul.item-detail-img img"):
        src = img.get("src", "")
        if src and "makeshop" in src and src not in images:
            images.append(src)
    if not images:
        return None

    # ── Item ID from URL ──────────────────────────────────────
    item_id_match = re.search(r"/item/(\d+)", url)
    item_id = item_id_match.group(1) if item_id_match else url.split("/")[-1]

    # ── Description (raw Japanese) ────────────────────────────
    desc_el = soup.select_one("div.item-detail-txt span.content")
    description_raw = desc_el.get_text(separator=" ", strip=True) if desc_el else ""

    # ── Sold date: look for date announcement in description ──
    sold_date = None
    date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", description_raw)
    if date_match:
        try:
            sold_date = datetime(int(date_match.group(1)),
                                 int(date_match.group(2)),
                                 int(date_match.group(3)))
        except ValueError:
            pass

    # ── Translate description ─────────────────────────────────
    description = translate_text(description_raw) if description_raw else ""

    return {
        "title":       title,
        "price_yen":   price_yen,
        "price_usd":   round(price_yen / YEN_TO_USD, 2),
        "description": description,
        "images":      json.dumps(images[:10]),
        "sold_date":   sold_date,
        "store":       "berberjin",
    }


def run_bbj_scrape(db) -> int:
    """Scrape BerBerJin sold category, newest-first. Stops after 5 consecutive known items."""
    s = make_bbj_session()
    added = 0
    consecutive_existing = 0
    page = 1

    logging.info("=" * 50)
    logging.info("BERBERJIN SCRAPE START")

    while True:
        logging.info(f"BBJ category page {page}...")
        item_urls = fetch_bbj_category_page(s, page)
        if not item_urls:
            logging.info(f"BBJ: no items on page {page}, stopping.")
            break

        for url in item_urls:
            item_id_match = re.search(r"/item/(\d+)", url)
            item_id = item_id_match.group(1) if item_id_match else None

            # Check duplicate by store + external_id pattern via title proxy
            # Use item_id as a quick pre-check against existing items
            existing = db.query(Item).filter(
                Item.store == "berberjin",
                Item.title.like(f"%{item_id}%") if item_id else Item.title == ""
            ).first() if item_id else None

            # Proper check: query by store + item_id stored in description field marker
            # Actually check by store + title after parsing
            time.sleep(BBJ_ITEM_DELAY_SECONDS)
            data = parse_bbj_item_page(s, url)

            if data is None:
                # Skipped (not sold, ¥0, or below threshold) — don't count as existing
                continue

            # Check if already in DB by store + title
            if db.query(Item).filter(
                Item.store == "berberjin",
                Item.title == data["title"]
            ).first():
                consecutive_existing += 1
                logging.info(f"BBJ: already exists '{data['title'][:50]}' "
                             f"({consecutive_existing}/5)")
                if consecutive_existing >= 5:
                    logging.info("BBJ: 5 consecutive existing items, stopping.")
                    goto_done = True
                    break
                continue

            consecutive_existing = 0
            try:
                item = Item(**data)
                db.add(item)
                db.commit()
                added += 1
                logging.info(f"BBJ: added '{data['title'][:50]}' ¥{data['price_yen']:,}")
            except Exception as e:
                db.rollback()
                logging.error(f"BBJ: insert failed for '{data['title'][:50]}': {e}")
        else:
            goto_done = False

        if goto_done:
            break

        page += 1
        time.sleep(BBJ_PAGE_DELAY_SECONDS)

    logging.info("=" * 50)
    logging.info("BERBERJIN SCRAPE COMPLETE")
    logging.info(f"  Items added: {added}")
    logging.info("=" * 50)
    return added
