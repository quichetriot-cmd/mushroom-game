import requests
from bs4 import BeautifulSoup
import json
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

BASE_URL = "https://vintage-mushroom.net"
CATEGORY_URL = f"{BASE_URL}/?mode=cate&csid=0&cbid=1809250&sort=n"
YEN_TO_USD = 150
MIN_PRICE_YEN = 65000
MAX_CONSECUTIVE_EXISTING = 5

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


def item_exists(db, title: str, price_yen: int) -> bool:
    from models import Item
    existing = db.query(Item).filter(
        Item.title == title,
        Item.price_yen == price_yen
    ).first()
    return existing is not None


def add_item_to_db(db, item_data: dict) -> bool:
    from models import Item
    try:
        sold_date = datetime.strptime(item_data['sold_date'], '%Y-%m-%d').date()
        item = Item(
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


def run_incremental_scrape(db, max_pages: int = 50) -> int:
    new_items = 0
    consecutive_existing = 0

    logging.info("="*50)
    logging.info("INCREMENTAL SCRAPE - Checking for new items...")
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

            if item_exists(db, item_data['title'], item_data['price_yen']):
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
    logging.info("FULL SCRAPE - Getting all items...")
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

            if item_exists(db, item_data['title'], item_data['price_yen']):
                skipped += 1
            else:
                if add_item_to_db(db, item_data):
                    new_items += 1
                    logging.info(f"  ${item_data['price_usd']:,} - {item_data['title'][:50]}...")

            time.sleep(0.5)

        logging.info(f"Page {page} done. Total: {new_items} new, {skipped} skipped")
        time.sleep(1)

    logging.info("="*50)
    logging.info(f"FULL SCRAPE COMPLETE")
    logging.info(f"  New items: {new_items}")
    logging.info(f"  Skipped (duplicates): {skipped}")
    logging.info(f"  Total in database: {get_item_count(db)}")
    logging.info("="*50)

    return new_items


def run_smart_scrape(db) -> int:
    count = get_item_count(db)
    if count == 0:
        logging.info("Database is empty. Running full scrape...")
        return run_full_scrape(db)
    else:
        logging.info(f"Database has {count} items. Running incremental scrape...")
        return run_incremental_scrape(db)
