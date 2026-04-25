import json
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)

    store = Column(String, index=True, default="mushroom", nullable=False, server_default="mushroom")

    title = Column(String, index=True)
    price_yen = Column(Integer, index=True)
    price_usd = Column(Float)

    description = Column(Text)

    images = Column(Text)

    sold_date = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("store", "title"),
    )

    def get_images(self):
        try:
            return json.loads(self.images) if self.images else []
        except Exception:
            return []


class TrackedProduct(Base):
    __tablename__ = "tracked_products"

    id = Column(Integer, primary_key=True, index=True)

    store = Column(String, index=True, nullable=False)
    external_id = Column(String, index=True)
    handle = Column(String, index=True, nullable=False)
    title = Column(String, index=True)

    price_yen = Column(Integer, index=True)
    price_usd = Column(Float)
    is_available = Column(Boolean, nullable=False, default=True)

    description = Column(Text)
    tags = Column(Text)
    images = Column(Text)

    created_at = Column(DateTime)
    published_at = Column(DateTime)
    updated_at = Column(DateTime)

    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sold_detected_at = Column(DateTime)
    exported_item_id = Column(Integer)

    __table_args__ = (
        UniqueConstraint("store", "handle"),
    )

    def get_images(self):
        try:
            return json.loads(self.images) if self.images else []
        except Exception:
            return []

    def get_tags(self):
        try:
            return json.loads(self.tags) if self.tags else []
        except Exception:
            return []
