"""Ingest historical Grand Prix weather observations from OpenF1.

OpenF1 weather observations are sampled about once per minute. The database
stores source timestamps as UTC-naive timestamps because the existing weather
schema uses PostgreSQL TIMESTAMP WITHOUT TIME ZONE; this conversion is explicit.

Run from the repository root:
    python -m backend.ingestion.fetch_weather 2023 --round 1 --dry-run
    python -m backend.ingestion.fetch_weather 2023 --round 1
    python -m backend.ingestion.fetch_weather 2023
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from ..database.connection import get_engine
from .fetch_race_laps import (
    _get_openf1_json,
    _optional_bool,
    _optional_float,
    fetch_openf1_race_sessions,
    get_scheduled_race_sessions,
    match_openf1_race_session,
)
from .utils import insert_weather


def parse_utc_timestamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp and return UTC as a naive datetime for TIMESTAMP.

    OpenF1 documents weather dates as UTC ISO-8601 timestamps. Naive source
    timestamps, if encountered, are interpreted as UTC rather than local time.
    """
    if value is None:
        return None

    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            timestamp_text = str(value).strip()
            if not timestamp_text:
                return None
            if timestamp_text.endswith("Z"):
                timestamp_text = timestamp_text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(timestamp_text)
    except (TypeError, ValueError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def normalize_weather(
    weather_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize weather observations while retaining nullable sensor fields.

    Rows without a usable timestamp are excluded because the timestamp forms
    the weather table's idempotent key with session_id. Other missing sensor
    values are preserved as NULL, not imputed.
    """
    if not weather_rows:
        raise ValueError("OpenF1 returned an empty weather table for this race.")

    stats = {
        "source_rows": len(weather_rows),
        "normalized_rows": 0,
        "excluded_invalid_timestamp_rows": 0,
        "missing_air_temperature_rows": 0,
        "missing_track_temperature_rows": 0,
        "missing_humidity_rows": 0,
        "missing_rainfall_rows": 0,
        "missing_wind_speed_rows": 0,
    }
    records: list[dict[str, Any]] = []

    for row in weather_rows:
        recorded_at = parse_utc_timestamp(row.get("date"))
        if recorded_at is None:
            stats["excluded_invalid_timestamp_rows"] += 1
            continue

        record = {
            "recorded_at": recorded_at,
            "air_temp": _optional_float(row.get("air_temperature")),
            "track_temp": _optional_float(row.get("track_temperature")),
            "humidity": _optional_float(row.get("humidity")),
            "rainfall": _optional_bool(row.get("rainfall")),
            "wind_speed": _optional_float(row.get("wind_speed")),
        }
        for field, stat_name in (
            ("air_temp", "missing_air_temperature_rows"),
            ("track_temp", "missing_track_temperature_rows"),
            ("humidity", "missing_humidity_rows"),
            ("rainfall", "missing_rainfall_rows"),
            ("wind_speed", "missing_wind_speed_rows"),
        ):
            if record[field] is None:
                stats[stat_name] += 1

        records.append(record)

    stats["normalized_rows"] = len(records)
    if not records:
        raise ValueError(
            "OpenF1 returned weather rows, but none had a valid timestamp."
        )
    return records, stats


def fetch_openf1_weather(session_key: int | str) -> list[dict[str, Any]]:
    """Fetch weather observations for one OpenF1 session and reject empty results."""
    rows = _get_openf1_json("weather", {"session_key": session_key})
    if not rows:
        raise ValueError(
            f"OpenF1 returned no weather observations for session_key={session_key}; "
            "refusing to treat missing source data as a successfully ingested session."
        )
    return rows


def _persist_weather(engine, session_id: int, records: list[dict[str, Any]]) -> None:
    """Upsert the observations for one race session in a single transaction."""
    with engine.begin() as connection:
        for record in records:
            insert_weather(
                connection,
                session_id=session_id,
                recorded_at=record["recorded_at"],
                air_temp=record["air_temp"],
                track_temp=record["track_temp"],
                humidity=record["humidity"],
                rainfall=record["rainfall"],
                wind_speed=record["wind_speed"],
            )


def ingest_weather(
    year: int,
    *,
    round_number: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Ingest weather observations for one Grand Prix race or a whole season."""
    if year < 2023:
        raise ValueError("OpenF1's free historical dataset starts in 2023.")
    if round_number is not None and round_number < 1:
        raise ValueError("Round number must be a positive integer.")

    engine = get_engine()
    summary = {
        "sessions": 0,
        "source_rows": 0,
        "normalized_rows": 0,
        "excluded_invalid_timestamp_rows": 0,
        "missing_air_temperature_rows": 0,
        "missing_track_temperature_rows": 0,
        "missing_humidity_rows": 0,
        "missing_rainfall_rows": 0,
        "missing_wind_speed_rows": 0,
    }
    try:
        scheduled_sessions = get_scheduled_race_sessions(engine, year, round_number)
        openf1_sessions = fetch_openf1_race_sessions(year)
        print(f"Season {year}: {len(scheduled_sessions)} Race session(s) queued.")

        for scheduled in scheduled_sessions:
            round_no = int(scheduled["round_number"])
            event_name = str(scheduled["event_name"])
            session_id = int(scheduled["session_id"])
            print(f"Loading R{round_no:02d}: {event_name} weather from OpenF1 ...")

            try:
                source_session = match_openf1_race_session(event_name, openf1_sessions)
                session_key = source_session.get("session_key")
                if session_key is None:
                    raise ValueError("matched OpenF1 session has no session_key")

                weather_rows = fetch_openf1_weather(session_key)
                records, stats = normalize_weather(weather_rows)
                print(
                    f"  Source rows: {stats['source_rows']}; "
                    f"upsertable rows: {stats['normalized_rows']}; "
                    f"invalid timestamps excluded: "
                    f"{stats['excluded_invalid_timestamp_rows']}; "
                    f"missing air/track temperature: "
                    f"{stats['missing_air_temperature_rows']}/"
                    f"{stats['missing_track_temperature_rows']}; "
                    f"missing humidity/rainfall/wind: "
                    f"{stats['missing_humidity_rows']}/"
                    f"{stats['missing_rainfall_rows']}/"
                    f"{stats['missing_wind_speed_rows']}."
                )

                if not dry_run:
                    _persist_weather(engine, session_id, records)

                summary["sessions"] += 1
                for key in (
                    "source_rows",
                    "normalized_rows",
                    "excluded_invalid_timestamp_rows",
                    "missing_air_temperature_rows",
                    "missing_track_temperature_rows",
                    "missing_humidity_rows",
                    "missing_rainfall_rows",
                    "missing_wind_speed_rows",
                ):
                    summary[key] += stats[key]
            except Exception as exc:
                raise RuntimeError(
                    f"Weather ingestion failed at season {year}, "
                    f"round {round_no} ({event_name}): {exc}"
                ) from exc
    finally:
        engine.dispose()

    action = "validated only; no weather rows written" if dry_run else "upsert complete"
    print(
        f"Season {year} weather ingestion {action}. "
        f"Sessions: {summary['sessions']}; source rows: {summary['source_rows']}; "
        f"upsertable rows: {summary['normalized_rows']}."
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest OpenF1 historical weather observations for Grand Prix races."
    )
    parser.add_argument("year", type=int, help="Season year, 2023 or later")
    parser.add_argument(
        "--round",
        dest="round_number",
        type=int,
        help="Ingest one championship round first, for example --round 1.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate weather without writing weather rows.",
    )
    args = parser.parse_args()

    try:
        ingest_weather(
            args.year,
            round_number=args.round_number,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        raise SystemExit(f"Weather ingestion failed: {exc}") from exc


if __name__ == "__main__":
    main()
