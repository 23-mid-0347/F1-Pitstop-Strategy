"""
Creates all tables from the ORM models in models.py.
Alternative to running schema.sql by hand with psql — use this one
once you're wiring things into FastAPI.

Usage:
    python database/init_db.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import get_engine
from database.models import Base


def main():
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("All tables created (or already existed).")


if __name__ == "__main__":
    main()