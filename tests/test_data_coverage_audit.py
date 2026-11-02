import csv

from backend.analysis.audit_data_coverage import (
    ROW_FIELDS,
    render_markdown_report,
    summarize_coverage,
    write_reports,
)


def _row(**overrides):
    row = {
        "year": 2023,
        "round_number": 1,
        "event_name": "Bahrain Grand Prix",
        "session_id": 10,
        "lap_rows": 1058,
        "unique_laps": 1058,
        "missing_lap_time": 5,
        "missing_compound": 2,
        "missing_tyre_age": 3,
        "missing_stint_number": 3,
        "missing_lap_start_time": 0,
        "missing_lap_end_time": 0,
        "pit_stop_rows": 50,
        "unique_pit_stop_identities": 50,
        "missing_pit_lap": 0,
        "missing_pit_duration": 0,
        "weather_rows": 161,
        "timestamped_weather_rows": 161,
        "unique_weather_timestamps": 161,
        "missing_weather_timestamps": 0,
        "missing_air_temp": 0,
        "missing_track_temp": 0,
        "missing_humidity": 0,
        "missing_rainfall": 0,
        "missing_wind_speed": 0,
        "race_control_periods": 0,
        "inferred_period_ends": 0,
        "approximate_period_start_lap_mappings": 0,
        "approximate_period_end_lap_mappings": 0,
    }
    row.update(overrides)
    return row


def test_summarize_coverage_aggregates_counts_and_duplicate_identities():
    rows = [
        _row(),
        _row(
            round_number=2,
            event_name="Saudi Arabian Grand Prix",
            session_id=11,
            lap_rows=900,
            unique_laps=898,
            pit_stop_rows=24,
            unique_pit_stop_identities=24,
            weather_rows=0,
            timestamped_weather_rows=0,
            unique_weather_timestamps=0,
            race_control_periods=2,
            inferred_period_ends=1,
        ),
    ]

    summary = summarize_coverage(rows)

    assert summary["race_sessions"] == 2
    assert summary["lap_rows"] == 1958
    assert summary["unique_laps"] == 1956
    assert summary["duplicate_lap_identities"] == 2
    assert summary["pit_stop_rows"] == 74
    assert summary["unique_pit_stop_identities"] == 74
    assert summary["weather_rows"] == 161
    assert summary["rounds_with_weather"] == 1
    assert summary["race_control_periods"] == 2
    assert summary["rounds_with_race_control"] == 1
    assert summary["inferred_period_ends"] == 1


def test_markdown_report_discloses_raw_data_scope_and_missing_ingestion():
    report = render_markdown_report(
        2023,
        [_row()],
        generated_at="2026-11-02T00:00:00+00:00",
    )

    assert "# F1 Pit Stop Strategy Optimizer — Data Coverage Audit (2023)" in report
    assert "| Race-lap rows | 1058 |" in report
    assert "| Weather observations | 161 |" in report
    assert "| Race-control periods | 0 |" in report
    assert "not a cleaned modelling dataset" in report
    assert "not evidence that a race had no neutralisations" in report
    assert "not incremental time loss versus green-flag racing pace" in report


def test_write_reports_creates_csv_and_markdown(tmp_path):
    csv_path, markdown_path = write_reports(2023, [_row()], tmp_path)

    assert csv_path.exists()
    assert markdown_path.exists()
    assert markdown_path.read_text(encoding="utf-8").startswith(
        "# F1 Pit Stop Strategy Optimizer"
    )

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)
        assert reader.fieldnames == ROW_FIELDS
    assert len(rows) == 1
    assert rows[0]["round_number"] == "1"
    assert rows[0]["lap_rows"] == "1058"
    assert rows[0]["weather_rows"] == "161"
