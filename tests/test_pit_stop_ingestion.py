from contextlib import nullcontext

import pytest

from backend.ingestion import fetch_pit_stops


def _drivers():
    return [
        {
            "driver_number": 1,
            "name_acronym": "VER",
            "full_name": "Max Verstappen",
            "country_code": "NED",
        },
        {
            "driver_number": 11,
            "name_acronym": "PER",
            "full_name": "Sergio Perez",
            "country_code": "MEX",
        },
    ]


def test_normalize_pit_stops_sorts_and_numbers_stops_per_driver():
    pit_rows = [
        {
            "driver_number": 1,
            "lap_number": 20,
            "date": "2023-03-05T15:40:00+00:00",
            "lane_duration": 24.5,
        },
        {
            "driver_number": 11,
            "lap_number": 8,
            "date": "2023-03-05T15:10:00+00:00",
            "pit_duration": 23.0,
        },
        {
            "driver_number": 1,
            "lap_number": 10,
            "date": "2023-03-05T15:20:00+00:00",
            "pit_duration": 22.215,
        },
        {
            "driver_number": 99,
            "lap_number": 15,
            "date": "2023-03-05T15:30:00+00:00",
            "lane_duration": 25.0,
        },
    ]

    records, stats = fetch_pit_stops.normalize_pit_stops(pit_rows, _drivers())

    assert stats == {
        "source_rows": 4,
        "normalized_rows": 3,
        "excluded_missing_driver_code": 1,
        "missing_lap_number_rows": 0,
        "missing_duration_rows": 0,
    }
    by_driver = {}
    for record in records:
        by_driver.setdefault(record["driver_code"], []).append(record)

    assert by_driver["PER"][0]["stop_number"] == 1
    assert by_driver["PER"][0]["lap_number"] == 8
    assert by_driver["PER"][0]["duration_ms"] == 23000
    assert by_driver["VER"][0]["stop_number"] == 1
    assert by_driver["VER"][0]["lap_number"] == 10
    assert by_driver["VER"][0]["duration_ms"] == 22215
    assert by_driver["VER"][1]["stop_number"] == 2
    assert by_driver["VER"][1]["lap_number"] == 20
    assert by_driver["VER"][1]["duration_ms"] == 24500
    assert by_driver["VER"][0]["full_name"] == "Max Verstappen"
    assert by_driver["VER"][0]["nationality"] == "NED"


def test_normalize_pit_stops_preserves_missing_lap_and_duration():
    pit_rows = [
        {
            "driver_number": 1,
            "lap_number": None,
            "date": None,
            "lane_duration": None,
            "pit_duration": None,
        }
    ]

    records, stats = fetch_pit_stops.normalize_pit_stops(pit_rows, _drivers())

    assert len(records) == 1
    assert records[0]["lap_number"] is None
    assert records[0]["duration_ms"] is None
    assert stats["missing_lap_number_rows"] == 1
    assert stats["missing_duration_rows"] == 1


def test_dry_run_does_not_write_rows(monkeypatch):
    pit_rows = [
        {
            "driver_number": 1,
            "lap_number": 10,
            "date": "2023-03-05T15:20:00+00:00",
            "lane_duration": 22.215,
        }
    ]
    monkeypatch.setattr(
        fetch_pit_stops,
        "get_scheduled_race_sessions",
        lambda engine, year, round_number: [
            {"round_number": 1, "event_name": "Bahrain Grand Prix", "session_id": 11}
        ],
    )
    monkeypatch.setattr(
        fetch_pit_stops,
        "fetch_openf1_race_sessions",
        lambda year: [{"meeting_name": "Bahrain Grand Prix", "session_key": 123}],
    )
    monkeypatch.setattr(
        fetch_pit_stops,
        "match_openf1_race_session",
        lambda event_name, sessions: sessions[0],
    )
    monkeypatch.setattr(
        fetch_pit_stops,
        "fetch_openf1_pit_stops",
        lambda session_key: pit_rows,
    )
    monkeypatch.setattr(
        fetch_pit_stops,
        "fetch_openf1_drivers",
        lambda session_key: _drivers(),
    )

    class FakeEngine:
        disposed = False

        def begin(self):
            raise AssertionError("dry run must not open a write transaction")

        def dispose(self):
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(fetch_pit_stops, "get_engine", lambda: engine)

    summary = fetch_pit_stops.ingest_pit_stops(2023, round_number=1, dry_run=True)

    assert summary["sessions"] == 1
    assert summary["source_rows"] == 1
    assert summary["normalized_rows"] == 1
    assert engine.disposed is True


def test_ingestion_rejects_pre_2023_year():
    with pytest.raises(ValueError, match="starts in 2023"):
        fetch_pit_stops.ingest_pit_stops(2022, dry_run=True)


def test_persist_pit_stops_upserts_driver_and_lane_duration(monkeypatch):
    calls = []

    class FakeEngine:
        def begin(self):
            return nullcontext(object())

    records, _ = fetch_pit_stops.normalize_pit_stops(
        [
            {
                "driver_number": 1,
                "lap_number": 10,
                "date": "2023-03-05T15:20:00+00:00",
                "lane_duration": 22.215,
            }
        ],
        _drivers(),
    )
    monkeypatch.setattr(
        fetch_pit_stops,
        "upsert_driver",
        lambda connection, code, full_name, nationality: calls.append(
            ("driver", code, full_name, nationality)
        )
        or 42,
    )
    monkeypatch.setattr(
        fetch_pit_stops,
        "insert_pit_stop",
        lambda connection, **kwargs: calls.append(("pit", kwargs)),
    )

    fetch_pit_stops._persist_pit_stops(FakeEngine(), 99, records)

    assert calls[0] == ("driver", "VER", "Max Verstappen", "NED")
    assert calls[1][1] == {
        "session_id": 99,
        "driver_id": 42,
        "stop_number": 1,
        "lap": 10,
        "duration_ms": 22215,
    }
