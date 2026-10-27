"""Check that configured PostgreSQL credentials can execute a simple query.

Run from the backend directory with:
    python -m database.check_connection
"""
from sqlalchemy import text

from database.connection import get_engine


def main() -> None:
    engine = get_engine()
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1")).scalar_one()
            if result != 1:
                raise RuntimeError("Unexpected response from PostgreSQL.")
    except Exception as exc:
        raise SystemExit(
            "Database connection check failed. Confirm PostgreSQL is running and "
            "DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD in the root .env "
            "match a database role with access."
        ) from exc
    finally:
        engine.dispose()

    print("Database connection OK (SELECT 1 returned 1).")


if __name__ == "__main__":
    main()
