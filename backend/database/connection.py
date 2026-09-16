import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()


def get_engine():
    user = os.getenv("DB_USER")
    name = os.getenv("DB_NAME")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    password = os.getenv("DB_PASSWORD", "")

    missing = [var for var, val in [("DB_USER", user), ("DB_NAME", name)] if not val]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            f"Copy .env.example to .env and fill these in."
        )

    url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
    return create_engine(url)