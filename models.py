import json
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint
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
