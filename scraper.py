import requests
from bs4 import BeautifulSoup
import json
import time
import logging
from datetime import datetime, date
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = "https://vintage-mushroom.net"
CATEGORY_URL = f"{BASE_URL}/?mode=cate&csid=0&cbid=1809250&sort=n"
YEN_TO_USD = 150
MIN_PRICE_YEN = 65000

# Configure retry logic
session = requests.Session()
retry = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)


def translate_text(text: str) -> str:
    """Translate Japanese to English"""
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source='ja', target='en').translate(text)
        return result
    except Exception as e:
        logging.warning(f"Translation failed: {e}")
        return text


def parse_timestamp(url: str) -> Optional[str]:
    """Extract date from image URL timestamp"""
    if "cmsp_timestamp=" not in url:
        return None
    
    raw_timestamp = url.split("cmsp_timestamp=")[-1]
    if len(raw_timestamp) >= 8:
        try:
            return f"{raw_timestamp[:4]}-{raw_timestamp[4:6]}-{raw_timestamp[6:8]}"
        except:
            return None
    return None


def scrape_product_page(product_url: str) -> Optional[dict]:
    """Scrape a single product page"""
    try:
        response = session.get(product_url, timeout=30)
        soup = BeautifulSoup(response.content, 'html.parser')

        # Extract title
        title_tag = soup.find('meta', property='og:title')
        title = title_tag['content'].strip() if title_tag else None
        if not title:
            return None
        title = title.replace(' - 古着屋 ｜ mushroom(マッシュルーム)\u3000ヴィンテージクロージングストア', '').strip()

        # Extract description
        description_tag = soup.find('div', class_='product_exp')
        description = description_tag.text.strip() if description_tag else ''
        if description:
            description = ' '.join(description.replace('\u3000', ' ').split())
            description = translate_text(description)

        # Extract price
        price = None
        price_tag = soup.find('script', string=lambda t: t and 'sales_price_including_tax' in t)
        if price_tag:
            try:
                price_data = price_tag.string
                start = price_data.find('"sales_price_including_tax":') + len('"sales_price_including_tax":')
                end = price_data.find(',', start)
                price = int(price_data[start:end].strip().strip('"').replace(',', ''))
            except:
                pass

        if not price or price < MIN_PRICE_YEN:
            return None

        # Extract images
        images = []
        sold_date = None
        for img in soup.select('.product_image_thumb img')[:10]:
            img_src = img.get('src', '')
            if img_src.startswith('http'):
                img_url = img_src
            else:
                img_url = f"{BASE_URL}/{img_src.lstrip('/')}"
            images.append(img_url)
            
            # Get sold_date from first image timestamp
            if not sold_date:
                sold_date = parse_timestamp(img_url)

        if not images or not sold_date:
            return None

        return {
            'title': title,
            'price_yen': price,
            'price_usd': round(price / YEN_TO_USD),
            'description': description,
            'images': images,
            'sold_date': sold_date
        }

    except Exception as e:
        logging.error(f"Error scraping {product_url}: {e}")
        return None


def get_product_links(page_num: int) -> Optional[list]:
    """Get product links from a category page"""
    page_url = f"{CATEGORY_URL}&page={page_num}"
    
    try:
        response = session.get(page_url, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        links = [
            BASE_URL + item['href']
            for item in soup.select('.prd_lst_unit .prd_lst_link')
        ]
        return links
    except Exception as e:
        logging.error(f"Error fetching page {page_num}: {e}")
        return None


def item_exists(db, title: str, price_yen: int) -> bool:
    """Check if item already exists in database"""
    from main import Item
    existing = db.query(Item).filter(
        Item.title == title,
        Item.price_yen == price_yen
    ).first()
    return existing is not None


def add_item(db, item_data: dict) -> bool:
    """Add item to database"""
    from main import Item
    from datetime import datetime
    
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
        logging.error(f"Error adding item: {e}")
        db.rollback()
        return False


def run_incremental_scrape(db, max_pages: int = 10) -> int:
    """
    Run incremental scrape - stops when it finds existing items.
    Returns number of new items added.
    """
    new_items = 0
    consecutive_existing = 0
    max_consecutive = 5  # Stop after finding 5 existing items in a row
    
    logging.info("Starting incremental scrape...")
    
    for page in range(1, max_pages + 1):
        logging.info(f"Checking page {page}...")
        
        links = get_product_links(page)
        if not links:
            logging.info(f"No links found on page {page}, stopping.")
            break
        
        for link in links:
            item_data = scrape_product_page(link)
            
            if not item_data:
                continue
            
            # Check if already exists
            if item_exists(db, item_data['title'], item_data['price_yen']):
                consecutive_existing += 1
                logging.info(f"Found existing item ({consecutive_existing}): {item_data['title'][:40]}...")
                
                if consecutive_existing >= max_consecutive:
                    logging.info(f"Found {max_consecutive} consecutive existing items. Stopping scrape.")
                    return new_items
            else:
                consecutive_existing = 0  # Reset counter
                if add_item(db, item_data):
                    new_items += 1
                    logging.info(f"Added new item: {item_data['title'][:40]}...")
            
            time.sleep(0.5)  # Be nice to the server
        
        time.sleep(1)  # Pause between pages
    
    logging.info(f"Scrape complete. Added {new_items} new items.")
    return new_items


def run_full_scrape(db, max_pages: int = 500) -> int:
    """
    Run full scrape of all pages (for initial population).
    Returns number of items added.
    """
    new_items = 0
    
    logging.info(f"Starting full scrape (up to {max_pages} pages)...")
    
    for page in range(1, max_pages + 1):
        logging.info(f"Scraping page {page}/{max_pages}...")
        
        links = get_product_links(page)
        if not links:
            logging.info(f"No links found on page {page}, stopping.")
            break
        
        for link in links:
            item_data = scrape_product_page(link)
            
            if not item_data:
                continue
            
            if not item_exists(db, item_data['title'], item_data['price_yen']):
                if add_item(db, item_data):
                    new_items += 1
            
            time.sleep(0.5)
        
        logging.info(f"Page {page} done. Total new items: {new_items}")
        time.sleep(1)
    
    logging.info(f"Full scrape complete. Added {new_items} items.")
    return new_items


if __name__ == "__main__":
    # For testing
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    
    from main import SessionLocal
    db = SessionLocal()
    
    if args.full:
        run_full_scrape(db, args.pages)
    else:
        run_incremental_scrape(db, args.pages)
    
    db.close()
