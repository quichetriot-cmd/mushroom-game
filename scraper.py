import json
import re
import time

import requests
from bs4 import BeautifulSoup

from deep_translator import GoogleTranslator

from models import Item


BASE_URL = "https://vintage-mushroom.net"

MIN_PRICE_YEN = 65000
YEN_TO_USD = 150

MAX_CONSECUTIVE_EXISTING = 5


HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9"
}


translation_cache = {}

session = requests.Session()
session.headers.update(HEADERS)


def translate(text):

    if not text:
        return ""

    if text in translation_cache:
        return translation_cache[text]

    try:
        translated = GoogleTranslator(source="ja", target="en").translate(text)
    except Exception:
        translated = text

    translation_cache[text] = translated

    return translated


def clean_text(text):

    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def get_soup(url):

    r = session.get(url, timeout=(10, 30))

    r.raise_for_status()

    return BeautifulSoup(r.text, "html.parser")


def parse_price(text):

    text = re.sub(r"[^\d]", "", text)

    if not text:
        return 0

    return int(text)


def run_incremental_scrape(db):

    print("Starting scrape")

    page = 1

    consecutive_existing = 0

    while True:

        url = f"{BASE_URL}/sold/page/{page}"

        soup = get_soup(url)

        items = soup.select(".product")

        if not items:
            break

        for product in items:

            title_el = product.select_one(".product-title")

            price_el = product.select_one(".price")

            if not title_el or not price_el:
                continue

            title = clean_text(title_el.get_text())

            price_yen = parse_price(price_el.get_text())

            if price_yen < MIN_PRICE_YEN:
                continue

            existing = db.query(Item).filter_by(title=title, price_yen=price_yen).first()

            if existing:
                consecutive_existing += 1

                if consecutive_existing >= MAX_CONSECUTIVE_EXISTING:
                    print("Reached existing threshold — stopping scrape")
                    return

                continue

            consecutive_existing = 0

            product_link = product.select_one("a")

            if not product_link:
                continue

            href = product_link.get("href")

            product_url = BASE_URL + href

            try:

                product_soup = get_soup(product_url)

                desc_el = product_soup.select_one(".product-description")

                description = clean_text(desc_el.get_text()) if desc_el else ""

                description = translate(description)

                images = []

                for img in product_soup.select(".product-gallery img"):

                    src = img.get("src")

                    if src and src not in images:
                        images.append(src)

                item = Item(
                    title=title,
                    price_yen=price_yen,
                    price_usd=price_yen / YEN_TO_USD,
                    description=description,
                    images=json.dumps(images)
                )

                db.add(item)

                db.commit()

                print("Added:", title)

                time.sleep(1)

            except Exception as e:

                print("Error scraping product:", e)

        page += 1

        time.sleep(2)
