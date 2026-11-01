from contextlib import nullcontext
from datetime import date, datetime

import pandas as pd
import pytest

from backend.ingestion import fetch_schedule


def _schedule_row(
    round_number: int = 1,
    event_name: str = "Bahrain Grand Prix",
) -> dict:
    return {
        "RoundNumber": round_number,
        "Country": "Bahrain",
        "Location": "Sakhir",
        "OfficialEventName": "FORMULA 1 GULF AIR BAHRAIN GRAND PRIX",
        "EventName": event_name,
        "EventDate": pd.Timestamp("2023-03-05"),
        "EventFormat": "conventional",
        "Session1": "Practice 1",
        "Session1Date": pd.Timestamp("2023-03-03 14:30:00", tz="Asia/Bahrain"),
        "Session1DateUtc": pd.Timestamp("2023-03-03 11:30:00"),
        "Session2": "Qualifying",
        "Session2Date": pd.Timestamp("2023-03-04 18:00:00", tz="Asia/Bahrain"),
        "Session2DateUtc": pd.Timestamp("2023-03-04 15:00:00"),
        "Session3": None,
        "Session3Date": pd.NaT,
        "Session3DateUtc": pd.NaT,
        "Session4": None,
        "Session4Date": pd.NaT,
        "Session4DateUtc": pd.NaT,
        "Session5": "Race",
        "Session5Date": pd.Timestamp("2023-03-05 18:00:00", tz="Asia/Bahrain"),
        "Session5DateUtc": pd.Timestamp("2023-03-05 15:00:00"),
    }


def test_normalize_schedule_preserves_event_metadata_and_utc_session_times():
    records, skipped = fetch_schedule.normalize_schedule(pd.DataFrame([_schedule_row()]))

    assert skipped == 0
    assert len(records) == 1
    event = records[0]
    assert event["round_number"] == 1
    assert event["event_name"] == "Bahrain Grand Prix"
    assert event["event_date"] == date(2023, 3, 5)
    assert event["country"] == "Bahrain"
    assert event["location"] == "Sakhir"
    assert event["official_event_name"] == "FORMULA 1 GULF AIR BAHRAIN GRAND PRIX"
    assert event["event_format"] == "conventional"
    assert [session["session_type"] for session in event["sessions"]] == [
        "Practice 1",
        "Qualifying",
        "Race",
    ]
    assert event["sessions"][0]["session_date"] == datetime(2023, 3, 3, 11, 30)


def test_normalize_schedule_skips_round_zero_testing_rows():
    schedule = pd.DataFrame([_schedule_row(round_number=0, event_name="Pre-Season Testing"), _schedule_row()])

    records, skipped = fetch_schedule.normalize_schedule(schedule)

    assert len(records) == 1
    assert records[0]["round_number"] == 1
    assert skipped == 1


def test_normalize_schedule_rejects_duplicate_rounds():
    schedule = pd.DataFrame([_schedule_row(), _schedule_row(event_name="Duplicate Event")])

    with pytest.raises(ValueError, match="Duplicate championship round"):
        fetch_schedule.normalize_schedule(schedule)


def test_normalize_schedule_rejects_events_without_sessions():
    row = _schedule_row()
    for number in range(1, 6):
        row[f"Session{number}"] = None
        row[f"Session{number}Date"] = pd.NaT
        row[f"Session{number}DateUtc"] = pd.NaT

    with pytest.raises(ValueError, match="has no session names"):
        fetch_schedule.normalize_schedule(pd.DataFrame([row]))


def test_ingest_schedule_upserts_records_and_disposes_engine(monkeypatch):
    schedule = pd.DataFrame([_schedule_row()])
    calls = []

    class FakeEngine:
        disposed = False

        def begin(self):
            return nullcontext(object())

        def dispose(self):
            self.disposed = True

    fake_engine = FakeEngine()
    monkeypatch.setattr(fetch_schedule, "setup_fastf1_cache", lambda: None)
    monkeypatch.setattr(
        fetch_schedule.fastf1,
        "get_event_schedule",
        lambda year, include_testing: schedule,
    )
    monkeypatch.setattr(fetch_schedule, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(fetch_schedule, "upsert_season", lambda conn, year: 123)

    def fake_upsert_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return 456

    def fake_upsert_session(conn, **kwargs):
        calls.append(("session", kwargs))
        return 789

    monkeypatch.setattr(fetch_schedule, "upsert_event", fake_upsert_event)
    monkeypatch.setattr(fetch_schedule, "upsert_session", fake_upsert_session)

    summary = fetch_schedule.ingest_schedule(2023)

    assert summary == {"events": 1, "sessions": 3, "skipped_testing_rows": 0}
    assert calls[0][0] == "event"
    assert calls[0][1]["event_name"] == "Bahrain Grand Prix"
    assert calls[0][1]["circuit_id"] is None
    assert calls[0][1]["location"] == "Sakhir"
    assert [call[1]["session_type"] for call in calls[1:]] == [
        "Practice 1",
        "Qualifying",
        "Race",
    ]
    assert fake_engine.disposed is True


def test_dry_run_does_not_open_database(monkeypatch):
    schedule = pd.DataFrame([_schedule_row()])
    monkeypatch.setattr(fetch_schedule, "setup_fastf1_cache", lambda: None)
    monkeypatch.setattr(
        fetch_schedule.fastf1,
        "get_event_schedule",
        lambda year, include_testing: schedule,
    )

    def database_should_not_be_opened():
        raise AssertionError("dry-run must not connect to PostgreSQL")

    monkeypatch.setattr(fetch_schedule, "get_engine", database_should_not_be_opened)

    summary = fetch_schedule.ingest_schedule(2023, dry_run=True)

    assert summary["events"] == 1
    assert summary["sessions"] == 3
