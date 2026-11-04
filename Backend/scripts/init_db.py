"""Create the tables. Safe to run again: python scripts/init_db.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from app.config import BASE_DIR
from app.store import Store

load_dotenv(BASE_DIR / ".env")
Store(os.environ["DATABASE_URL"]).init_schema()
print("Tables are ready.")
