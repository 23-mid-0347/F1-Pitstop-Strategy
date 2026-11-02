from contextlib import nullcontext
from datetime import datetime

import pytest

from backend.ingestion import fetch_weather


def _weather_row(
    date="2023-05-07T18:42:25.233000+00:00",
    *,
    air_temperature=27.8,
    track_temperature=52.5,
    humidity=58,
    rainfall=0,
    wind_speed=2.4,
):
    return {
        "date": date,
        "air_temperature": air_temperature,
        "track_temperature": track_temperature,
        "humidity": humidity,
        "rainfall": rainfall,
        "wind_speed": wind_speed,
    }


def test_parse_utc_timestamp_returns_utc_naive_datetime():
    assert fetch_weather.parse_utc_timestamp(
        "2023-05-07T18:42:25.233000+00:00"
    ) == datetime(2023, 5, 7, 18, 42, 25, 233000)

    # 20:42 at UTC+02:00 is 18:42 UTC.
    assert fetch_weather.parse_utc_timestamp(
        "2023-05-07T20:42:25.233+02:00"
    ) == datetime(2023, 5, 7, 18, 42, 25, 233000)


def test_parse_utc_timestamp_handles_z_naive_and_invalid_values():
    assert fetch_weather.parse_utc_timestamp(
        "2023-05-07T18:42:25Z"
    ) == datetime(2023, 5, 7, 18, 42, 25)
    assert fetch_weather.parse_utc_timestamp(
        "2023-05-07T18:42:25"
    ) == datetime(2023, 5, 7, 18, 42, 25)
    assert fetch_weather.parse_utc_timestamp(None) is None
    assert fetch_weather.parse_utc_timestamp("not-a-timestamp") is None


def test_normalize_weather_converts_fields_and_preserves_nulls():
    rows = [
        _weather_row(),
        _weather_row(
            date="2023-05-07T18:43:25Z",
            air_temperature=None,
            track_temperature="",
            humidity=None,
            rainfall=None,
            wind_speed="invalid",
        ),
        _weather_row(date="invalid"),
    ]

    records, stats = fetch_weather.normalize_weather(rows)

    assert len(records) == 2
    assert records[0] == {
        "recorded_at": datetime(2023, 5, 7, 18, 42, 25, 233000),
        "air_temp": pytest.approx(27.8),
        "track_temp": pytest.approx(52.5),
        "humidity": pytest.approx(58.0),
        "rainfall": False,
        "wind_speed": pytest.approx(2.4),
    }
    assert records[1] == {
        "recorded_at": datetime(2023, 5, 7, 18, 43, 25),
        "air_temp": None,
        "track_temp": None,
        "humidity": None,
        "rainfall": None,
        "wind_speed": None,
    }
    assert stats == {
        "source_rows": 3,
        "normalized_rows": 2,
        "excluded_invalid_timestamp_rows": 1,
        "missing_air_temperature_rows": 1,
        "missing_track_temperature_rows": 1,
        "missing_humidity_rows": 1,
        "missing_rainfall_rows": 1,
        "missing_wind_speed_rows": 1,
    }


def test_normalize_weather_rejects_empty_or_all_invalid_rows():
    with pytest.raises(ValueError, match="empty weather table"):
        fetch_weather.normalize_weather([])

    with pytest.raises(ValueError, match="none had a valid timestamp"):
        fetch_weather.normalize_weather([_weather_row(date="invalid")])


def test_fetch_openf1_weather_rejects_empty_source_response(monkeypatch):
    monkeypatch.setattr(fetch_weather, "_get_openf1_json", lambda *args: [])

    with pytest.raises(ValueError, match="no weather observations"):
        fetch_weather.fetch_openf1_weather(1234)


def test_dry_run_does_not_write_rows(monkeypatch):
    monkeypatch.setattr(
        fetch_weather,
        "get_scheduled_race_sessions",
        lambda engine, year, round_number: [
            {"round_number": 1, "event_name": "Bahrain Grand Prix", "session_id": 11}
        ],
    )
    monkeypatch.setattr(
        fetch_weather,
        "fetch_openf1_race_sessions",
        lambda year: [{"session_key": 1234}],
    )
    monkeypatch.setattr(
        fetch_weather,
        "match_openf1_race_session",
        lambda event_name, sessions: {"session_key": 1234},
    )
    monkeypatch.setattr(
        fetch_weather,
        "fetch_openf1_weather",
        lambda session_key: [_weather_row()],
    )

    class FakeEngine:
        disposed = False

        def begin(self):
            raise AssertionError("dry run must not open a write transaction")

        def dispose(self):
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(fetch_weather, "get_engine", lambda: engine)

    summary = fetch_weather.ingest_weather(2023, round_number=1, dry_run=True)

    assert summary["sessions"] == 1
    assert summary["source_rows"] == 1
    assert summary["normalized_rows"] == 1
    assert engine.disposed is True


def test_ingestion_rejects_year_before_openf1_weather_history():
    with pytest.raises(ValueError, match="starts in 2023"):
        fetch_weather.ingest_weather(2022, dry_run=True)


def test_persist_weather_upserts_all_supported_fields(monkeypatch):
    calls = []

    class FakeEngine:
        def begin(self):
            return nullcontext(object())

    monkeypatch.setattr(
        fetch_weather,
        "insert_weather",
        lambda connection, **kwargs: calls.append(kwargs),
    )
    records, _ = fetch_weather.normalize_weather([_weather_row()])

    fetch_weather._persist_weather(FakeEngine(), 99, records)

    assert calls == [
        {
            "session_id": 99,
            "recorded_at": datetime(2023, 5, 7, 18, 42, 25, 233000),
            "air_temp": pytest.approx(27.8),
            "track_temp": pytest.approx(52.5),
            "humidity": pytest.approx(58.0),
            "rainfall": False,
            "wind_speed": pytest.approx(2.4),
        }
    ]
