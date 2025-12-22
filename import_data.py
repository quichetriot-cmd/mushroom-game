"""
Import existing JSON data into database.
Run this once to populate the database with your existing scraped data.
"""
import json
import sys
from datetime import datetime
from sqlalchemy.orm import Session

def import_json(db: Session, json_path: str) -> int:
    """Import items from JSON file into database"""
    from main import Item
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"Loading {len(data)} items from {json_path}")
    
    imported = 0
    skipped = 0
    
    for item_data in data:
        # Check if already exists
        existing = db.query(Item).filter(
            Item.title == item_data.get('title'),
            Item.price_yen == item_data.get('price_yen')
        ).first()
        
        if existing:
            skipped += 1
            continue
        
        try:
            sold_date = datetime.strptime(item_data['sold_date'], '%Y-%m-%d').date()
            
            item = Item(
                title=item_data.get('title', ''),
                price_yen=item_data.get('price_yen', 0),
                price_usd=item_data.get('price_usd', 0),
                description=item_data.get('description', ''),
                images=json.dumps(item_data.get('images', [])),
                sold_date=sold_date
            )
            db.add(item)
            imported += 1
            
            if imported % 500 == 0:
                db.commit()
                print(f"  Imported {imported} items...")
                
        except Exception as e:
            print(f"  Error importing item: {e}")
            continue
    
    db.commit()
    print(f"Done. Imported: {imported}, Skipped (duplicates): {skipped}")
    return imported


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_data.py <path_to_json>")
        sys.exit(1)
    
    json_path = sys.argv[1]
    
    from main import SessionLocal
    db = SessionLocal()
    
    try:
        import_json(db, json_path)
    finally:
        db.close()
