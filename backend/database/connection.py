import os

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine

load_dotenv()


def get_engine():
    user = os.getenv("DB_USER")
    name = os.getenv("DB_NAME")
    host = os.getenv("DB_HOST", "localhost")
    raw_port = os.getenv("DB_PORT", "5432")
    password = os.getenv("DB_PASSWORD", "")

    missing = [var for var, val in (("DB_USER", user), ("DB_NAME", name)) if not val]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill these in."
        )

    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise EnvironmentError("DB_PORT must be an integer, for example 5432.") from exc

    # URL.create handles special characters in usernames/passwords without
    # requiring callers to URL-encode credentials manually.
    database_url = URL.create(
        drivername="postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=name,
    )
    return create_engine(database_url, pool_pre_ping=True)
