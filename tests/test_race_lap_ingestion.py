from contextlib import nullcontext
from datetime import datetime

import pandas as pd
import pytest

from backend.ingestion import fetch_race_laps


def _lap_row(
    driver: str = "VER",
    lap_number=1,
    lap_time=pd.Timedelta(seconds=90, milliseconds=123),
) -> dict:
    return {
        "Driver": driver,
        "LapNumber": lap_number,
        "LapTime": lap_time,
        "Compound": "SOFT",
        "Stint": 1,
        "TyreLife": 3.0,
        "FreshTyre": True,
        "PitInTime": pd.NaT,
        "PitOutTime": pd.NaT,
        "TrackStatus": "1",
        "IsAccurate": True,
        "Deleted": False,
        "DeletedReason": None,
        "FastF1Generated": False,
        "LapStartTime": pd.Timedelta(minutes=2),
        "Time": pd.Timedelta(minutes=3, seconds=30, milliseconds=123),
    }


def test_normalize_laps_preserves_timing_tyre_and_quality_fields():
    records, stats = fetch_race_laps.normalize_laps(pd.DataFrame([_lap_row()]))

    assert stats == {
        "source_rows": 1,
        "normalized_rows": 1,
        "excluded_missing_driver_code": 0,
        "excluded_invalid_lap_number": 0,
        "missing_lap_time_rows": 0,
    }
    lap = records[0]
    assert lap["driver_code"] == "VER"
    assert lap["lap_number"] == 1
    assert lap["lap_time_ms"] == 90123
    assert lap["tire_compound"] == "SOFT"
    assert lap["stint_number"] == 1
    assert lap["tyre_age"] == 3.0
    assert lap["fresh_tyre"] is True
    assert lap["pit_in_lap"] is False
    assert lap["pit_out_lap"] is False
    assert lap["track_status"] == "1"
    assert lap["is_accurate"] is True
    assert lap["is_deleted"] is False
    assert lap["fastf1_generated"] is False
    assert lap["lap_start_time_ms"] == 120000
    assert lap["lap_end_time_ms"] == 210123


def test_normalize_laps_preserves_missing_lap_time_and_pit_out_flag():
    row = _lap_row(lap_number=2, lap_time=pd.NaT)
    row["PitOutTime"] = pd.Timedelta(seconds=100)
    row["FreshTyre"] = False
    row["IsAccurate"] = False
    row["DeletedReason"] = "track limits"
    records, stats = fetch_race_laps.normalize_laps(pd.DataFrame([row]))

    assert len(records) == 1
    assert records[0]["lap_time_ms"] is None
    assert records[0]["pit_out_lap"] is True
    assert records[0]["fresh_tyre"] is False
    assert records[0]["is_accurate"] is False
    assert records[0]["deleted_reason"] == "track limits"
    assert stats["missing_lap_time_rows"] == 1


def test_normalize_laps_logs_rows_excluded_for_missing_identity():
    missing_driver = _lap_row(driver=None, lap_number=2)
    invalid_lap = _lap_row(driver="HAM", lap_number=float("nan"))
    fractional_lap = _lap_row(driver="LEC", lap_number=2.5)

    records, stats = fetch_race_laps.normalize_laps(
        pd.DataFrame([_lap_row(), missing_driver, invalid_lap, fractional_lap])
    )

    assert len(records) == 1
    assert stats["source_rows"] == 4
    assert stats["normalized_rows"] == 1
    assert stats["excluded_missing_driver_code"] == 1
    assert stats["excluded_invalid_lap_number"] == 2


def test_normalize_laps_rejects_duplicate_driver_lap_key():
    with pytest.raises(ValueError, match="duplicate lap identity"):
        fetch_race_laps.normalize_laps(pd.DataFrame([_lap_row(), _lap_row()]))


def test_normalize_laps_requires_identity_and_laptime_columns():
    with pytest.raises(ValueError, match="missing required columns"):
        fetch_race_laps.normalize_laps(pd.DataFrame({"Driver": ["VER"]}))


def test_dry_run_does_not_write_rows_or_open_transactions(monkeypatch):
    laps = pd.DataFrame([_lap_row()])
    monkeypatch.setattr(fetch_race_laps, "setup_fastf1_cache", lambda: None)
    monkeypatch.setattr(
        fetch_race_laps,
        "get_scheduled_race_sessions",
        lambda engine, year, round_number: [
            {"round_number": 1, "event_name": "Bahrain Grand Prix", "session_id": 11}
        ],
    )
    monkeypatch.setattr(fetch_race_laps, "fetch_fastf1_race_laps", lambda year, round_number: laps)

    class FakeEngine:
        disposed = False

        def begin(self):
            raise AssertionError("dry run must not open a write transaction")

        def dispose(self):
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(fetch_race_laps, "get_engine", lambda: engine)

    summary = fetch_race_laps.ingest_race_laps(2023, round_number=1, dry_run=True)

    assert summary["sessions"] == 1
    assert summary["source_rows"] == 1
    assert summary["normalized_rows"] == 1
    assert engine.disposed is True


def test_persist_laps_upserts_driver_and_forwards_tyre_age(monkeypatch):
    calls = []

    class FakeEngine:
        def begin(self):
            return nullcontext(object())

    record = fetch_race_laps.normalize_laps(pd.DataFrame([_lap_row()]))[0][0]
    monkeypatch.setattr(
        fetch_race_laps,
        "upsert_driver",
        lambda connection, code, full_name, nationality: calls.append(
            ("driver", code, full_name, nationality)
        ) or 42,
    )
    monkeypatch.setattr(
        fetch_race_laps,
        "insert_lap",
        lambda connection, **kwargs: calls.append(("lap", kwargs)),
    )

    fetch_race_laps._persist_laps(FakeEngine(), 99, [record])

    assert calls[0] == ("driver", "VER", "VER", None)
    lap_call = calls[1][1]
    assert lap_call["session_id"] == 99
    assert lap_call["driver_id"] == 42
    assert lap_call["tyre_age"] == 3.0
    assert lap_call["tire_compound"] == "SOFT"
    assert lap_call["is_accurate"] is True
