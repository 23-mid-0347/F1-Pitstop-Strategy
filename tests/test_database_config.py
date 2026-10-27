import pytest

from backend.database.connection import get_engine


def test_missing_required_database_settings_are_reported(monkeypatch):
    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)

    with pytest.raises(EnvironmentError, match="DB_USER, DB_NAME"):
        get_engine()


def test_engine_uses_psycopg3_and_preserves_special_characters(monkeypatch):
    monkeypatch.setenv("DB_USER", "test_user")
    monkeypatch.setenv("DB_NAME", "test_db")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word:with?symbols")

    engine = get_engine()
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.username == "test_user"
        assert engine.url.password == "p@ss/word:with?symbols"
        assert engine.url.database == "test_db"
        assert engine.url.port == 5432
    finally:
        engine.dispose()


def test_non_integer_database_port_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_USER", "test_user")
    monkeypatch.setenv("DB_NAME", "test_db")
    monkeypatch.setenv("DB_PORT", "not-a-port")

    with pytest.raises(EnvironmentError, match="DB_PORT must be an integer"):
        get_engine()
