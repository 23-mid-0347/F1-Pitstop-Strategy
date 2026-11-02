"""Audit ingested F1 data coverage from PostgreSQL and write CSV/Markdown reports.

Run from the repository root:
    python -m backend.analysis.audit_data_coverage 2023

This is an inventory of stored raw observations. It does not define or apply the
modelling exclusions, fit models, or claim that source records are complete.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import text

from ..database.connection import get_engine


COVERAGE_SQL = text("""
WITH lap_stats AS (
    SELECT
        session_id,
        COUNT(*) AS lap_rows,
        COUNT(DISTINCT (driver_id, lap_number)) AS unique_laps,
        COUNT(*) FILTER (WHERE lap_time_ms IS NULL) AS missing_lap_time,
        COUNT(*) FILTER (WHERE tire_compound IS NULL) AS missing_compound,
        COUNT(*) FILTER (WHERE tyre_age IS NULL) AS missing_tyre_age,
        COUNT(*) FILTER (WHERE stint_number IS NULL) AS missing_stint_number,
        COUNT(*) FILTER (WHERE lap_start_time_ms IS NULL) AS missing_lap_start_time,
        COUNT(*) FILTER (WHERE lap_end_time_ms IS NULL) AS missing_lap_end_time
    FROM lap_times
    GROUP BY session_id
),
pit_stats AS (
    SELECT
        session_id,
        COUNT(*) AS pit_stop_rows,
        COUNT(DISTINCT (driver_id, stop_number)) AS unique_pit_stop_identities,
        COUNT(*) FILTER (WHERE lap IS NULL) AS missing_pit_lap,
        COUNT(*) FILTER (WHERE duration_ms IS NULL) AS missing_pit_duration
    FROM pit_stops
    GROUP BY session_id
),
weather_stats AS (
    SELECT
        session_id,
        COUNT(*) AS weather_rows,
        COUNT(recorded_at) AS timestamped_weather_rows,
        COUNT(DISTINCT recorded_at) AS unique_weather_timestamps,
        COUNT(*) FILTER (WHERE recorded_at IS NULL) AS missing_weather_timestamps,
        COUNT(*) FILTER (WHERE air_temp IS NULL) AS missing_air_temp,
        COUNT(*) FILTER (WHERE track_temp IS NULL) AS missing_track_temp,
        COUNT(*) FILTER (WHERE humidity IS NULL) AS missing_humidity,
        COUNT(*) FILTER (WHERE rainfall IS NULL) AS missing_rainfall,
        COUNT(*) FILTER (WHERE wind_speed IS NULL) AS missing_wind_speed
    FROM weather
    GROUP BY session_id
),
race_control_stats AS (
    SELECT
        session_id,
        COUNT(*) AS race_control_periods,
        COUNT(*) FILTER (WHERE end_inferred) AS inferred_period_ends,
        COUNT(*) FILTER (
            WHERE start_lap_method <> 'source_lap_number'
        ) AS approximate_period_start_lap_mappings,
        COUNT(*) FILTER (
            WHERE end_lap_method NOT IN ('source_lap_number', 'race_horizon')
        ) AS approximate_period_end_lap_mappings
    FROM safety_car_periods
    GROUP BY session_id
)
SELECT
    sea.year,
    e.round AS round_number,
    e.event_name,
    s.session_id,
    COALESCE(ls.lap_rows, 0) AS lap_rows,
    COALESCE(ls.unique_laps, 0) AS unique_laps,
    COALESCE(ls.missing_lap_time, 0) AS missing_lap_time,
    COALESCE(ls.missing_compound, 0) AS missing_compound,
    COALESCE(ls.missing_tyre_age, 0) AS missing_tyre_age,
    COALESCE(ls.missing_stint_number, 0) AS missing_stint_number,
    COALESCE(ls.missing_lap_start_time, 0) AS missing_lap_start_time,
    COALESCE(ls.missing_lap_end_time, 0) AS missing_lap_end_time,
    COALESCE(ps.pit_stop_rows, 0) AS pit_stop_rows,
    COALESCE(ps.unique_pit_stop_identities, 0) AS unique_pit_stop_identities,
    COALESCE(ps.missing_pit_lap, 0) AS missing_pit_lap,
    COALESCE(ps.missing_pit_duration, 0) AS missing_pit_duration,
    COALESCE(ws.weather_rows, 0) AS weather_rows,
    COALESCE(ws.timestamped_weather_rows, 0) AS timestamped_weather_rows,
    COALESCE(ws.unique_weather_timestamps, 0) AS unique_weather_timestamps,
    COALESCE(ws.missing_weather_timestamps, 0) AS missing_weather_timestamps,
    COALESCE(ws.missing_air_temp, 0) AS missing_air_temp,
    COALESCE(ws.missing_track_temp, 0) AS missing_track_temp,
    COALESCE(ws.missing_humidity, 0) AS missing_humidity,
    COALESCE(ws.missing_rainfall, 0) AS missing_rainfall,
    COALESCE(ws.missing_wind_speed, 0) AS missing_wind_speed,
    COALESCE(rc.race_control_periods, 0) AS race_control_periods,
    COALESCE(rc.inferred_period_ends, 0) AS inferred_period_ends,
    COALESCE(rc.approximate_period_start_lap_mappings, 0)
        AS approximate_period_start_lap_mappings,
    COALESCE(rc.approximate_period_end_lap_mappings, 0)
        AS approximate_period_end_lap_mappings
FROM seasons sea
JOIN events e ON e.season_id = sea.season_id
JOIN sessions s ON s.event_id = e.event_id AND s.session_type = 'Race'
LEFT JOIN lap_stats ls ON ls.session_id = s.session_id
LEFT JOIN pit_stats ps ON ps.session_id = s.session_id
LEFT JOIN weather_stats ws ON ws.session_id = s.session_id
LEFT JOIN race_control_stats rc ON rc.session_id = s.session_id
WHERE sea.year = :year
ORDER BY e.round
""")

ROW_FIELDS = [
    "year",
    "round_number",
    "event_name",
    "session_id",
    "lap_rows",
    "unique_laps",
    "missing_lap_time",
    "missing_compound",
    "missing_tyre_age",
    "missing_stint_number",
    "missing_lap_start_time",
    "missing_lap_end_time",
    "pit_stop_rows",
    "unique_pit_stop_identities",
    "missing_pit_lap",
    "missing_pit_duration",
    "weather_rows",
    "timestamped_weather_rows",
    "unique_weather_timestamps",
    "missing_weather_timestamps",
    "missing_air_temp",
    "missing_track_temp",
    "missing_humidity",
    "missing_rainfall",
    "missing_wind_speed",
    "race_control_periods",
    "inferred_period_ends",
    "approximate_period_start_lap_mappings",
    "approximate_period_end_lap_mappings",
]

COUNT_FIELDS = [field for field in ROW_FIELDS if field not in {"year", "round_number", "event_name", "session_id"}]


def collect_coverage(year: int) -> list[dict[str, Any]]:
    """Fetch one row per scheduled Grand Prix Race session from the database."""
    engine = get_engine()
    try:
        with engine.connect() as connection:
            rows = connection.execute(COVERAGE_SQL, {"year": year}).mappings().all()
        result = [dict(row) for row in rows]
    finally:
        engine.dispose()

    if not result:
        raise ValueError(
            f"No scheduled Grand Prix Race sessions found for {year}; "
            "load and verify the schedule first."
        )
    return result


def summarize_coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Sum coverage counters across race sessions without joining raw tables together."""
    summary = {field: sum(int(row.get(field) or 0) for row in rows) for field in COUNT_FIELDS}
    summary["race_sessions"] = len(rows)
    summary["rounds_with_laps"] = sum(int(row.get("lap_rows") or 0) > 0 for row in rows)
    summary["rounds_with_weather"] = sum(int(row.get("weather_rows") or 0) > 0 for row in rows)
    summary["rounds_with_race_control"] = sum(
        int(row.get("race_control_periods") or 0) > 0 for row in rows
    )
    summary["duplicate_lap_identities"] = summary["lap_rows"] - summary["unique_laps"]
    summary["duplicate_pit_stop_identities"] = (
        summary["pit_stop_rows"] - summary["unique_pit_stop_identities"]
    )
    summary["duplicate_weather_timestamps"] = (
        summary["timestamped_weather_rows"] - summary["unique_weather_timestamps"]
    )
    return summary


def render_markdown_report(
    year: int,
    rows: Sequence[Mapping[str, Any]],
    *,
    generated_at: str | None = None,
) -> str:
    """Create an auditable coverage report, explicitly distinguishing inventory from cleaning."""
    summary = summarize_coverage(rows)
    generated = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# F1 Pit Stop Strategy Optimizer — Data Coverage Audit ({year})",
        "",
        f"- Generated (UTC): {generated}",
        "- Report scope: raw ingested records for scheduled Grand Prix Race sessions.",
        "- This report is an inventory, not a cleaned modelling dataset or a source-completeness certification.",
        "",
        "## Season summary",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Scheduled Race sessions | {summary['race_sessions']} |",
        f"| Sessions with lap rows | {summary['rounds_with_laps']} |",
        f"| Race-lap rows | {summary['lap_rows']} |",
        f"| Unique driver/lap identities | {summary['unique_laps']} |",
        f"| Missing lap times | {summary['missing_lap_time']} |",
        f"| Missing compounds | {summary['missing_compound']} |",
        f"| Missing tyre age | {summary['missing_tyre_age']} |",
        f"| Missing stint numbers | {summary['missing_stint_number']} |",
        f"| Missing lap start offsets | {summary['missing_lap_start_time']} |",
        f"| Missing lap end offsets | {summary['missing_lap_end_time']} |",
        f"| Pit-stop rows | {summary['pit_stop_rows']} |",
        f"| Unique pit-stop identities | {summary['unique_pit_stop_identities']} |",
        f"| Pit stops missing lap number | {summary['missing_pit_lap']} |",
        f"| Pit stops missing duration | {summary['missing_pit_duration']} |",
        f"| Weather observations | {summary['weather_rows']} |",
        f"| Sessions with weather | {summary['rounds_with_weather']} |",
        f"| Weather rows missing timestamp | {summary['missing_weather_timestamps']} |",
        f"| Weather rows missing air temperature | {summary['missing_air_temp']} |",
        f"| Weather rows missing track temperature | {summary['missing_track_temp']} |",
        f"| Weather rows missing humidity | {summary['missing_humidity']} |",
        f"| Weather rows missing rainfall | {summary['missing_rainfall']} |",
        f"| Weather rows missing wind speed | {summary['missing_wind_speed']} |",
        f"| Race-control periods | {summary['race_control_periods']} |",
        f"| Sessions with race-control periods | {summary['rounds_with_race_control']} |",
        f"| Period ends inferred | {summary['inferred_period_ends']} |",
        f"| Duplicate lap identities (rows minus unique) | {summary['duplicate_lap_identities']} |",
        f"| Duplicate pit-stop identities (rows minus unique) | {summary['duplicate_pit_stop_identities']} |",
        f"| Duplicate non-null weather timestamps (rows minus unique) | {summary['duplicate_weather_timestamps']} |",
        "",
        "## Per-race inventory",
        "",
        "| Rnd | Race | Laps | Unique laps | Missing lap time | Pit stops | Unique stops | Weather | SC/VSC/red-flag periods |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['round_number']} | {row['event_name']} | {row['lap_rows']} | "
            f"{row['unique_laps']} | {row['missing_lap_time']} | {row['pit_stop_rows']} | "
            f"{row['unique_pit_stop_identities']} | {row['weather_rows']} | "
            f"{row['race_control_periods']} |"
        )

    lines.extend([
        "",
        "## Exclusions and data-quality caveats",
        "",
        "- No clean modelling dataset or exclusion/attrition manifest is produced by this audit. Do not treat the row counts here as the final modelling sample.",
        "- Race-lap ingestion preserves nullable source fields and skips records that cannot be assigned a usable driver code or positive integer lap number. The skipped-record counts were printed by the ingestion run but are not persisted as a queryable audit table.",
        "- Race-control periods are zero until the race-control loader has been successfully run and its output validated. A lack of stored periods is not evidence that a race had no neutralisations.",
        "- Weather is currently only partially ingested; absence of weather rows for a race is not evidence of dry conditions.",
        "- Pit-stop duration is source-reported pit-lane duration, not incremental time loss versus green-flag racing pace.",
        "- Before modelling, add explicit exclusions for pit-in/out laps, neutralised laps, invalid timings, and unsupported tyre-age cases; record counts by race and exclusion reason.",
        "- Validate selected records against the original sources and freeze season-wise or rolling race splits before model fitting.",
        "",
        "## Files",
        "",
        f"- `data_coverage_{year}.csv`: one record per Race session with source-field coverage and missingness counters.",
        f"- `data_coverage_{year}.md`: this report.",
        "",
    ])
    return "\n".join(lines)


def write_reports(
    year: int,
    rows: Sequence[Mapping[str, Any]],
    output_dir: str | Path = "reports",
) -> tuple[Path, Path]:
    """Write the per-race CSV and summary Markdown report."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    csv_path = target / f"data_coverage_{year}.csv"
    markdown_path = target / f"data_coverage_{year}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=ROW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, 0) for field in ROW_FIELDS})

    markdown_path.write_text(render_markdown_report(year, rows), encoding="utf-8")
    return csv_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit raw F1 race data coverage and write CSV/Markdown reports."
    )
    parser.add_argument("year", type=int, help="Season year already loaded in PostgreSQL")
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Output directory relative to the current directory (default: reports)",
    )
    args = parser.parse_args()

    rows = collect_coverage(args.year)
    csv_path, markdown_path = write_reports(args.year, rows, args.output_dir)
    summary = summarize_coverage(rows)
    print(f"Data coverage audit completed for {args.year}.")
    print(f"Race sessions: {summary['race_sessions']}")
    print(f"Lap rows: {summary['lap_rows']} ({summary['unique_laps']} unique identities)")
    print(
        f"Pit stops: {summary['pit_stop_rows']} "
        f"({summary['unique_pit_stop_identities']} unique identities)"
    )
    print(
        f"Weather observations: {summary['weather_rows']} across "
        f"{summary['rounds_with_weather']} race session(s)"
    )
    print(
        f"Race-control periods: {summary['race_control_periods']} across "
        f"{summary['rounds_with_race_control']} race session(s)"
    )
    print(f"CSV: {csv_path}")
    print(f"Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
