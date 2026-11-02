from contextlib import nullcontext

import pytest

from backend.ingestion import fetch_pit_stops


def _drivers():
    return [
        {
            "driver_number": "max_verstappen",
            "name_acronym": "VER",
            "full_name": "Max Verstappen",
            "country_code": "Dutch",
        },
        {
            "driver_number": "sergio_perez",
            "name_acronym": "PER",
            "full_name": "Sergio Perez",
            "country_code": "Mexican",
        },
    ]


def _jolpica_pit(driver_id, lap, stop, duration, time):
    return {
        "driver_number": driver_id,
        "lap_number": str(lap) if lap is not None else None,
        "stop": str(stop) if stop is not None else None,
        "duration": duration,
        "time": time,
    }


def test_duration_parser_handles_seconds_and_minute_second_formats():
    assert fetch_pit_stops._duration_seconds("25.885") == pytest.approx(25.885)
    assert fetch_pit_stops._duration_seconds("1:02.300") == pytest.approx(62.3)
    assert fetch_pit_stops._duration_seconds("40:55.302") == pytest.approx(2455.302)
    assert fetch_pit_stops._duration_seconds(None) is None
    assert fetch_pit_stops._duration_seconds("invalid") is None


def test_fetch_jolpica_pit_stops_maps_driver_records(monkeypatch):
    payloads = {
        "2023/1/pitstops": {
            "MRData": {
                "RaceTable": {
                    "Races": [{
                        "raceName": "Bahrain Grand Prix",
                        "PitStops": [
                            {
                                "driverId": "max_verstappen",
                                "lap": "19",
                                "stop": "1",
                                "time": "18:37:03",
                                "duration": "24.500",
                            }
                        ],
                    }]
                }
            }
        },
        "2023/1/results": {
            "MRData": {
                "RaceTable": {
                    "Races": [{
                        "raceName": "Bahrain Grand Prix",
                        "Results": [{
                            "Driver": {
                                "driverId": "max_verstappen",
                                "code": "VER",
                                "givenName": "Max",
                                "familyName": "Verstappen",
                                "nationality": "Dutch",
                            }
                        }],
                    }]
                }
            }
        },
    }
    calls = []

    def fake_get(path, params):
        calls.append((path, params))
        return payloads[path]

    monkeypatch.setattr(fetch_pit_stops, "_get_jolpica_json", fake_get)

    pit_rows, driver_rows = fetch_pit_stops.fetch_jolpica_pit_stops(2023, 1)

    assert pit_rows == [
        {
            "driver_number": "max_verstappen",
            "lap_number": "19",
            "stop": "1",
            "time": "18:37:03",
            "duration": "24.500",
        }
    ]
    assert driver_rows[0]["name_acronym"] == "VER"
    assert driver_rows[0]["full_name"] == "Max Verstappen"
    assert calls == [
        ("2023/1/pitstops", {"limit": 100}),
        ("2023/1/results", {"limit": 100}),
    ]


def test_normalize_pit_stops_uses_source_stop_numbers_and_sorts_per_driver():
    pit_rows = [
        _jolpica_pit("max_verstappen", 20, 2, "24.500", "18:40:00"),
        _jolpica_pit("sergio_perez", 8, 1, "23.000", "18:10:00"),
        _jolpica_pit("max_verstappen", 10, 1, "22.215", "18:20:00"),
        _jolpica_pit("unknown_driver", 15, 1, "25.000", "18:30:00"),
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
    assert by_driver["VER"][0]["nationality"] == "Dutch"


def test_normalize_pit_stops_preserves_missing_lap_and_duration():
    pit_rows = [
        _jolpica_pit("max_verstappen", None, 1, None, None),
    ]

    records, stats = fetch_pit_stops.normalize_pit_stops(pit_rows, _drivers())

    assert len(records) == 1
    assert records[0]["lap_number"] is None
    assert records[0]["duration_ms"] is None
    assert records[0]["stop_number"] == 1
    assert stats["missing_lap_number_rows"] == 1
    assert stats["missing_duration_rows"] == 1


def test_normalize_pit_stops_rejects_duplicate_source_stop_identity():
    pit_rows = [
        _jolpica_pit("max_verstappen", 10, 1, "22.0", "18:20:00"),
        _jolpica_pit("max_verstappen", 11, 1, "23.0", "18:30:00"),
    ]
    with pytest.raises(ValueError, match="Duplicate pit-stop identity"):
        fetch_pit_stops.normalize_pit_stops(pit_rows, _drivers())


def test_dry_run_does_not_write_rows(monkeypatch):
    pit_rows = [
        _jolpica_pit("max_verstappen", 10, 1, "22.215", "18:20:00"),
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
        "fetch_jolpica_pit_stops",
        lambda year, round_number: (pit_rows, _drivers()),
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


def test_ingestion_rejects_year_before_jolpica_pit_history():
    with pytest.raises(ValueError, match="starts in 2011"):
        fetch_pit_stops.ingest_pit_stops(2010, dry_run=True)


def test_persist_pit_stops_upserts_driver_and_duration(monkeypatch):
    calls = []

    class FakeEngine:
        def begin(self):
            return nullcontext(object())

    records, _ = fetch_pit_stops.normalize_pit_stops(
        [_jolpica_pit("max_verstappen", 10, 1, "22.215", "18:20:00")],
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

    assert calls[0] == ("driver", "VER", "Max Verstappen", "Dutch")
    assert calls[1][1] == {
        "session_id": 99,
        "driver_id": 42,
        "stop_number": 1,
        "lap": 10,
        "duration_ms": 22215,
    }
