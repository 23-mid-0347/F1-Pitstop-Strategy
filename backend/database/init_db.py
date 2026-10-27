"""
Create all tables by executing schema.sql directly against PostgreSQL.

Run from the backend directory:
    python -m database.init_db
"""
import os

from sqlalchemy import text

from database.connection import get_engine


def main() -> None:
    engine = get_engine()
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    with open(schema_path, encoding="utf-8") as schema_file:
        ddl = schema_file.read()

    # schema.sql contains individual DDL statements and no procedural blocks.
    # Execute them separately so the script does not depend on multi-statement
    # handling in a particular PostgreSQL driver/protocol mode.
    statements = [statement.strip() for statement in ddl.split(";") if statement.strip()]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)

    engine.dispose()
    print(f"Database schema initialized ({len(statements)} statements).")


if __name__ == "__main__":
    main()
