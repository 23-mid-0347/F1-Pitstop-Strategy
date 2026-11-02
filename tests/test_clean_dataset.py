import csv
import hashlib
import json
from datetime import date

import pytest

from backend.analysis.build_clean_dataset import (
    EXCLUSION_RULES,
    MODEL_FIELDS,
    clean_lap_rows,
    exclusion_reason,
    render_attrition_report,
    summarize_cleaning,
    write_cleaning_artifacts,
)


def _row(**overrides):
    row = {
        "year": 2023,
        "round_number": 1,
        "event_name": "Bahrain Grand Prix",
        "event_date": date(2023, 3, 5),
        "circuit_id": None,
        "circuit_name": None,
        "session_id": 5,
        "driver_code": "VER",
        "lap_number": 10,
        "lap_time_ms": 92000,
        "compound": "SOFT",
        "tyre_age": 9.0,
        "stint_number": 1,
        "fresh_tyre": True,
        "pit_in_lap": None,
        "pit_out_lap": False,
        "track_status": None,
        "is_accurate": None,
        "is_deleted": None,
        "deleted_reason": None,
        "fastf1_generated": None,
        "lap_start_time_ms": None,
        "lap_end_time_ms": None,
    }
    row.update(overrides)
    return row


def test_exclusion_reason_accepts_valid_lap_even_when_nonessential_flags_missing():
    row = _row(stint_number=None, pit_in_lap=None, track_status=None)
    assert exclusion_reason(row) is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"is_deleted": True}, "deleted_lap"),
        ({"is_accurate": False}, "inaccurate_lap"),
        ({"pit_in_lap": True}, "pit_in_lap"),
        ({"pit_out_lap": True}, "pit_out_lap"),
        ({"track_status": "4"}, "non_clear_or_unsupported_track_status"),
        ({"lap_time_ms": None}, "missing_or_invalid_lap_time"),
        ({"lap_time_ms": 0}, "missing_or_invalid_lap_time"),
        ({"compound": None}, "missing_or_unsupported_compound"),
        ({"compound": "UNKNOWN"}, "missing_or_unsupported_compound"),
        ({"tyre_age": None}, "missing_or_invalid_tyre_age"),
        ({"tyre_age": -1}, "missing_or_invalid_tyre_age"),
    ],
)
def test_exclusion_rules(overrides, expected):
    assert exclusion_reason(_row(**overrides)) == expected


def test_first_matching_rule_wins():
    row = _row(is_deleted=True, pit_out_lap=True, lap_time_ms=None)
    assert exclusion_reason(row) == "deleted_lap"


def test_clean_lap_rows_returns_seconds_normalizes_compound_and_audits_exclusions():
    raw = [
        _row(lap_number=10, compound=" soft "),
        _row(lap_number=11, lap_time_ms=None),
        _row(lap_number=12, pit_out_lap=True, tyre_age=None),
    ]

    included, excluded = clean_lap_rows(raw)

    assert len(included) == 1
    assert len(excluded) == 2
    assert included[0]["lap_time_ms"] == 92000
    assert included[0]["lap_time_seconds"] == pytest.approx(92.0)
    assert included[0]["compound"] == "SOFT"
    assert included[0]["circuit_name"] is None
    assert [item["exclusion_reason"] for item in excluded] == [
        "missing_or_invalid_lap_time",
        "pit_out_lap",
    ]


def test_exclusion_priority_is_documented_and_mutually_exclusive():
    raw = [_row(lap_time_ms=None), _row(compound=None), _row(tyre_age=None)]
    included, excluded = clean_lap_rows(raw)
    summary = summarize_cleaning(raw, included, excluded)

    assert len(included) + len(excluded) == len(raw)
    assert summary["exclusion_reason_counts"] == {
        "missing_or_invalid_lap_time": 1,
        "missing_or_invalid_tyre_age": 1,
        "missing_or_unsupported_compound": 1,
    }
    assert list(dict(EXCLUSION_RULES)) == [reason for reason, _desc in EXCLUSION_RULES]


def test_summarize_cleaning_counts_source_limitations_and_per_race_attrition():
    raw = [
        _row(lap_number=10),
        _row(round_number=2, event_name="Saudi Arabian Grand Prix", session_id=10, lap_number=11),
    ]
    included, excluded = clean_lap_rows(
        [raw[0], _row(round_number=2, event_name="Saudi Arabian Grand Prix", session_id=10, lap_time_ms=None)]
    )
    summary = summarize_cleaning(raw, included, excluded)

    assert summary["raw_rows"] == 2
    assert summary["included_rows"] == 1
    assert summary["excluded_rows"] == 1
    assert summary["race_sessions"] == 2
    assert summary["sessions_without_circuit_mapping"] == 2
    assert summary["missing_track_status_rows"] == 2
    assert summary["missing_pit_in_flag_rows"] == 2
    assert summary["missing_lap_start_offset_rows"] == 2
    assert summary["per_round"][2]["excluded_rows"] == 1


def test_attrition_report_documents_unavailable_event_and_circuit_data():
    raw = [_row()]
    included, excluded = clean_lap_rows(raw)
    report = render_attrition_report(
        2023, raw, included, excluded, generated_at="2026-11-02T00:00:00+00:00"
    )

    assert "Raw lap rows" in report
    assert "Included baseline rows" in report
    assert "Pit-in flags are missing for 1 / 1 rows" in report
    assert "Neutralised laps cannot be reliably excluded" in report
    assert "Formal circuit names are mapped for 0 / 1 race sessions" in report
    assert "not the final evaluation dataset" in report


def test_write_cleaning_artifacts_outputs_consistent_csv_and_manifest(tmp_path):
    raw = [
        _row(lap_number=10),
        _row(lap_number=11, lap_time_ms=None),
    ]
    included, excluded = clean_lap_rows(raw)
    data_dir = tmp_path / "data" / "processed"
    reports_dir = tmp_path / "reports"

    paths = write_cleaning_artifacts(
        2023, raw, included, excluded,
        data_dir=data_dir,
        reports_dir=reports_dir,
        generated_at="2026-11-02T00:00:00+00:00",
    )

    assert set(paths) == {"modeling_csv", "exclusions_csv", "attrition_report", "manifest_json"}
    with paths["modeling_csv"].open(newline="", encoding="utf-8") as stream:
        model_rows = list(csv.DictReader(stream))
    with paths["exclusions_csv"].open(newline="", encoding="utf-8") as stream:
        exclusion_rows = list(csv.DictReader(stream))
    manifest = json.loads(paths["manifest_json"].read_text(encoding="utf-8"))

    assert len(model_rows) == 1
    assert len(exclusion_rows) == 1
    assert exclusion_rows[0]["exclusion_reason"] == "missing_or_invalid_lap_time"
    assert set(model_rows[0]) == set(MODEL_FIELDS)
    assert manifest["raw_rows"] == 2
    assert manifest["included_rows"] == 1
    assert manifest["excluded_rows"] == 1
    assert manifest["artifacts"]["modeling_csv_sha256"] == hashlib.sha256(
        paths["modeling_csv"].read_bytes()
    ).hexdigest()
    assert manifest["artifacts"]["exclusions_csv_sha256"] == hashlib.sha256(
        paths["exclusions_csv"].read_bytes()
    ).hexdigest()
