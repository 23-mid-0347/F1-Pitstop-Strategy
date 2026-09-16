"""
Creates all tables by executing schema.sql directly against the database.

Usage:
    python database/init_db.py
"""
import os
from sqlalchemy import text

from database.connection import get_engine


def main():
    engine = get_engine()
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    with open(schema_path) as f:
        ddl = f.read()
    with engine.begin() as conn:
        conn.execute(text(ddl))
    print("All tables created (or already existed).")


if __name__ == "__main__":
    main()