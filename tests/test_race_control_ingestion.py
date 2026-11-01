from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest

from backend.ingestion import fetch_race_control


def _stamp(minutes=0, seconds=0):
    return datetime(2023, 3, 5, 15, 0, tzinfo=timezone.utc) + timedelta(
        minutes=minutes, seconds=seconds
    )


def _iso(value):
    return value.isoformat()


def _lap_rows(last_lap=6):
    rows = []
    for lap in range(1, last_lap + 1):
        # Multiple drivers make median lap-start mapping deterministic.
        for offset in (0, 2):
            rows.append({
                "lap_number": lap,
                "date_start": _iso(_stamp(minutes=lap - 1, seconds=offset)),
                "lap_duration": 60.0,
            })
    return rows


def _event(minutes, seconds, message, *, lap=None, flag=None, category="SafetyCar"):
    return {
        "date": _iso(_stamp(minutes=minutes, seconds=seconds)),
        "lap_number": lap,
        "message": message,
        "flag": flag,
        "category": category,
    }


def test_parse_utc_timestamp_normalizes_offsets_and_z_suffix():
    assert fetch_race_control.parse_utc_timestamp(
        "2023-03-05T15:00:00Z"
    ) == datetime(2023, 3, 5, 15, 0)
    assert fetch_race_control.parse_utc_timestamp(
        "2023-03-05T17:00:00+02:00"
    ) == datetime(2023, 3, 5, 15, 0)
    assert fetch_race_control.parse_utc_timestamp("invalid") is None
    assert fetch_race_control.parse_utc_timestamp(None) is None


def test_build_lap_timeline_uses_median_driver_lap_starts_and_horizon():
    timeline, max_lap, horizon = fetch_race_control.build_lap_timeline(_lap_rows(3))

    assert timeline == [
        (datetime(2023, 3, 5, 15, 0, 1), 1),
        (datetime(2023, 3, 5, 15, 1, 1), 2),
        (datetime(2023, 3, 5, 15, 2, 1), 3),
    ]
    assert max_lap == 3
    assert horizon == datetime(2023, 3, 5, 15, 3, 2)


def test_map_event_to_lap_prefers_source_lap_and_has_midpoint_fallback():
    timeline, max_lap, _horizon = fetch_race_control.build_lap_timeline(_lap_rows(3))

    assert fetch_race_control.map_event_to_lap(
        {"lap_number": "2", "date": _iso(_stamp(seconds=5))}, timeline, max_lap
    ) == (2, "source_lap_number")

    # Timeline median starts are 15:00:01 and 15:01:01.
    # Exactly 15:00:31 is the midpoint, which is assigned to the earlier lap.
    assert fetch_race_control.map_event_to_lap(
        {"lap_number": None, "date": _iso(_stamp(seconds=31))}, timeline, max_lap
    ) == (1, "nearest_median_lap_start")


def test_normalize_race_control_pairs_sc_vsc_and_red_flag_periods():
    messages = [
        _event(1, 10, "SAFETY CAR DEPLOYED", lap=2),
        _event(3, 10, "SAFETY CAR IN THIS LAP", lap=4),
        _event(4, 10, "VIRTUAL SAFETY CAR DEPLOYED"),
        _event(4, 20, "VIRTUAL SAFETY CAR ENDING"),
        _event(5, 10, "RED FLAG", flag="RED", category="Flag"),
        _event(5, 40, "GREEN FLAG", flag="GREEN", category="Flag"),
    ]

    periods, stats = fetch_race_control.normalize_race_control(messages, _lap_rows(6))

    assert [(period["period_type"], period["start_lap"], period["end_lap"]) for period in periods] == [
        ("SC", 2, 4),
        ("VSC", 5, 5),
        ("RedFlag", 6, 6),
    ]
    assert all(period["end_inferred"] is False for period in periods)
    assert periods[0]["start_lap_method"] == "source_lap_number"
    assert periods[0]["end_lap_method"] == "source_lap_number"
    assert periods[1]["start_lap_method"] == "nearest_median_lap_start"
    assert periods[1]["end_lap_method"] == "nearest_median_lap_start"
    assert stats["source_rows"] == 6
    assert stats["recognized_transitions"] == 6
    assert stats["periods"] == 3
    assert stats["approximate_lap_mappings"] == 4
    assert stats["inferred_end_periods"] == 0
    assert stats["unmatched_end_events"] == 0


def test_normalize_race_control_closes_open_period_at_race_horizon():
    messages = [_event(1, 10, "VIRTUAL SAFETY CAR DEPLOYED", lap=2)]

    periods, stats = fetch_race_control.normalize_race_control(messages, _lap_rows(6))

    assert len(periods) == 1
    assert periods[0]["period_type"] == "VSC"
    assert periods[0]["start_lap"] == 2
    assert periods[0]["end_lap"] == 6
    assert periods[0]["end_inferred"] is True
    assert periods[0]["end_lap_method"] == "race_horizon"
    assert stats["closed_at_race_horizon"] == 1


def test_normalize_race_control_marks_superseded_period_end_inferred():
    messages = [
        _event(1, 10, "VIRTUAL SAFETY CAR DEPLOYED", lap=2),
        _event(2, 10, "SAFETY CAR DEPLOYED", lap=3),
        _event(3, 10, "SAFETY CAR IN THIS LAP", lap=4),
    ]

    periods, stats = fetch_race_control.normalize_race_control(messages, _lap_rows(6))

    assert [(p["period_type"], p["start_lap"], p["end_lap"]) for p in periods] == [
        ("VSC", 2, 3),
        ("SC", 3, 4),
    ]
    assert periods[0]["end_inferred"] is True
    assert periods[1]["end_inferred"] is False
    assert stats["inferred_end_periods"] == 1


def test_normalize_race_control_counts_unmatched_end_and_rejects_empty_sources():
    periods, stats = fetch_race_control.normalize_race_control(
        [_event(1, 10, "SAFETY CAR IN THIS LAP", lap=2)],
        _lap_rows(3),
    )
    assert periods == []
    assert stats["unmatched_end_events"] == 1

    with pytest.raises(ValueError, match="no race-control messages"):
        fetch_race_control.normalize_race_control([], _lap_rows(3))

    with pytest.raises(ValueError, match="No usable lap_number/date_start"):
        fetch_race_control.normalize_race_control(
            [_event(1, 10, "SAFETY CAR DEPLOYED", lap=2)],
            [{"lap_number": 1, "date_start": None, "lap_duration": 60}],
        )


def test_invalid_timestamps_are_counted_for_transition_messages():
    messages = [
        {
            "date": "invalid",
            "lap_number": 2,
            "message": "SAFETY CAR DEPLOYED",
            "flag": None,
            "category": "SafetyCar",
        }
    ]
    periods, stats = fetch_race_control.normalize_race_control(messages, _lap_rows(3))

    assert periods == []
    assert stats["excluded_invalid_timestamp_rows"] == 1


def test_dry_run_does_not_persist_periods(monkeypatch):
    race_control_rows = [_event(1, 10, "SAFETY CAR DEPLOYED", lap=2)]
    monkeypatch.setattr(
        fetch_race_control,
        "get_scheduled_race_sessions",
        lambda engine, year, round_number: [
            {"round_number": 1, "event_name": "Bahrain Grand Prix", "session_id": 11}
        ],
    )
    monkeypatch.setattr(
        fetch_race_control,
        "fetch_openf1_race_sessions",
        lambda year: [{"session_key": 1234}],
    )
    monkeypatch.setattr(
        fetch_race_control,
        "match_openf1_race_session",
        lambda event_name, sessions: {"session_key": 1234},
    )
    monkeypatch.setattr(
        fetch_race_control,
        "fetch_openf1_race_control",
        lambda session_key: race_control_rows,
    )
    monkeypatch.setattr(
        fetch_race_control,
        "fetch_openf1_lap_boundaries",
        lambda session_key: _lap_rows(6),
    )

    class FakeEngine:
        disposed = False

        def begin(self):
            raise AssertionError("dry run must not open a write transaction")

        def dispose(self):
            self.disposed = True

    engine = FakeEngine()
    monkeypatch.setattr(fetch_race_control, "get_engine", lambda: engine)

    summary = fetch_race_control.ingest_race_control(
        2023, round_number=1, dry_run=True
    )

    assert summary["sessions"] == 1
    assert summary["source_rows"] == 1
    assert summary["periods"] == 1
    assert engine.disposed is True


def test_persist_periods_calls_idempotent_upsert(monkeypatch):
    calls = []

    class FakeEngine:
        def begin(self):
            return nullcontext(object())

    monkeypatch.setattr(
        fetch_race_control,
        "insert_safety_car_period",
        lambda connection, **kwargs: calls.append(kwargs),
    )
    periods, _stats = fetch_race_control.normalize_race_control(
        [
            _event(1, 10, "SAFETY CAR DEPLOYED", lap=2),
            _event(3, 10, "SAFETY CAR IN THIS LAP", lap=4),
        ],
        _lap_rows(6),
    )

    fetch_race_control._persist_periods(FakeEngine(), 99, periods)

    assert calls == [
        {
            "session_id": 99,
            "start_time": datetime(2023, 3, 5, 15, 1, 10),
            "end_time": datetime(2023, 3, 5, 15, 3, 10),
            "start_lap": 2,
            "end_lap": 4,
            "period_type": "SC",
            "end_inferred": False,
            "start_lap_method": "source_lap_number",
            "end_lap_method": "source_lap_number",
        }
    ]


def test_ingestion_rejects_year_before_openf1_history():
    with pytest.raises(ValueError, match="starts in 2023"):
        fetch_race_control.ingest_race_control(2022, dry_run=True)
