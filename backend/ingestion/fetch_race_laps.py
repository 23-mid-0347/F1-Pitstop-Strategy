"""Ingest FastF1 lap records for one or all Grand Prix race sessions.

Run from the repository root after the season schedule has been loaded:
    python -m backend.ingestion.fetch_race_laps 2023 --round 1 --dry-run
    python -m backend.ingestion.fetch_race_laps 2023 --round 1
    python -m backend.ingestion.fetch_race_laps 2023

Raw lap rows are stored with pit-lap, tyre-life, track-status, and quality fields.
This loader deliberately preserves slow, inaccurate, deleted, in-lap, and out-lap
records for a later, auditable cleaning stage instead of filtering them silently.
"""
from __future__ import annotations

import argparse
import math
from datetime import date, datetime
from typing import Any

import fastf1
import pandas as pd
from sqlalchemy import text

from ..database.connection import get_engine
from .utils import (
    insert_lap,
    ms_from_timedelta,
    setup_fastf1_cache,
    upsert_driver,
)

_REQUIRED_LAP_COLUMNS = {"Driver", "LapNumber", "LapTime"}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _clean_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    value = str(value).strip()
    return value or None


def _positive_int(value: Any) -> int | None:
    """Convert an exact, positive integer-like value without truncating floats."""
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not number.is_integer() or number < 1:
        return None
    return int(number)


def _optional_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _optional_bool(value: Any) -> bool | None:
    if _is_missing(value):
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        return None
    try:
        return bool(value)
    except (TypeError, ValueError):
        return None


def _timedelta_ms(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        delta = value if isinstance(value, pd.Timedelta) else pd.to_timedelta(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return ms_from_timedelta(delta)


def _time_flag(row: pd.Series, column_name: str) -> bool | None:
    """Return whether PitInTime/PitOutTime is present, or None if column is absent."""
    if column_name not in row.index:
        return None
    return not _is_missing(row[column_name])


def normalize_laps(laps: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize FastF1's lap table while retaining rows for later quality filtering.

    Only rows without a usable driver code or positive integer lap number are
    excluded, because they cannot be assigned the database's lap uniqueness key.
    Missing lap times and source quality flags remain nullable.
    """
    missing_columns = _REQUIRED_LAP_COLUMNS.difference(laps.columns)
    if missing_columns:
        raise ValueError(
            "FastF1 lap data is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )
    if laps.empty:
        raise ValueError("FastF1 returned an empty lap table for this race.")

    stats = {
        "source_rows": int(len(laps)),
        "normalized_rows": 0,
        "excluded_missing_driver_code": 0,
        "excluded_invalid_lap_number": 0,
        "missing_lap_time_rows": 0,
    }
    records: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, int]] = set()

    for _, row in laps.iterrows():
        driver_code = _clean_text(row.get("Driver"))
        if driver_code is None:
            stats["excluded_missing_driver_code"] += 1
            continue

        lap_number = _positive_int(row.get("LapNumber"))
        if lap_number is None:
            stats["excluded_invalid_lap_number"] += 1
            continue

        key = (driver_code, lap_number)
        if key in seen_keys:
            raise ValueError(
                "FastF1 returned duplicate lap identity "
                f"for driver {driver_code} lap {lap_number}."
            )
        seen_keys.add(key)

        lap_time_ms = _timedelta_ms(row.get("LapTime"))
        if lap_time_ms is None:
            stats["missing_lap_time_rows"] += 1

        record = {
            "driver_code": driver_code,
            "lap_number": lap_number,
            "lap_time_ms": lap_time_ms,
            "tire_compound": _clean_text(row.get("Compound")),
            "stint_number": _positive_int(row.get("Stint")),
            "tyre_age": _optional_float(row.get("TyreLife")),
            "fresh_tyre": _optional_bool(row.get("FreshTyre")),
            "pit_in_lap": _time_flag(row, "PitInTime"),
            "pit_out_lap": _time_flag(row, "PitOutTime"),
            "track_status": _clean_text(row.get("TrackStatus")),
            "is_accurate": _optional_bool(row.get("IsAccurate")),
            "is_deleted": _optional_bool(row.get("Deleted")),
            "deleted_reason": _clean_text(row.get("DeletedReason")),
            "fastf1_generated": _optional_bool(row.get("FastF1Generated")),
            "lap_start_time_ms": _timedelta_ms(row.get("LapStartTime")),
            "lap_end_time_ms": _timedelta_ms(row.get("Time")),
        }
        records.append(record)

    stats["normalized_rows"] = len(records)
    return records, stats


def get_scheduled_race_sessions(
    engine,
    year: int,
    round_number: int | None = None,
) -> list[dict[str, Any]]:
    """Get Race session IDs from the schedule already stored in PostgreSQL."""
    query = """
        SELECT e.round AS round_number, e.event_name, ss.session_id
        FROM seasons AS s
        JOIN events AS e ON e.season_id = s.season_id
        JOIN sessions AS ss ON ss.event_id = e.event_id
        WHERE s.year = :year
          AND ss.session_type = 'Race'
    """
    params: dict[str, int] = {"year": year}
    if round_number is not None:
        query += " AND e.round = :round_number"
        params["round_number"] = round_number
    query += " ORDER BY e.round"

    with engine.connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()

    result = [dict(row) for row in rows]
    if not result:
        if round_number is None:
            scope = f"season {year}"
        else:
            scope = f"season {year}, round {round_number}"
        raise ValueError(
            f"No scheduled Race session found for {scope}. "
            "Run the schedule ingestion and schema initializer first."
        )
    return result


def fetch_fastf1_race_laps(year: int, round_number: int) -> pd.DataFrame:
    """Fetch race lap data via FastF1 without loading car telemetry or weather."""
    session = fastf1.get_session(year, round_number, "R")
    session.load(laps=True, telemetry=False, weather=False, messages=True)
    return session.laps


def _persist_laps(engine, session_id: int, records: list[dict[str, Any]]) -> None:
    """Upsert one session in a single transaction."""
    with engine.begin() as connection:
        driver_ids: dict[str, int] = {}
        for record in records:
            code = record["driver_code"]
            if code not in driver_ids:
                # Driver-name enrichment is deferred. The required full_name falls
                # back to the source abbreviation rather than inserting NULL.
                driver_ids[code] = upsert_driver(
                    connection,
                    code=code,
                    full_name=code,
                    nationality=None,
                )

            insert_lap(
                connection,
                session_id=session_id,
                driver_id=driver_ids[code],
                lap_number=record["lap_number"],
                lap_time_ms=record["lap_time_ms"],
                tire_compound=record["tire_compound"],
                stint_number=record["stint_number"],
                tyre_age=record["tyre_age"],
                fresh_tyre=record["fresh_tyre"],
                pit_in_lap=record["pit_in_lap"],
                pit_out_lap=record["pit_out_lap"],
                track_status=record["track_status"],
                is_accurate=record["is_accurate"],
                is_deleted=record["is_deleted"],
                deleted_reason=record["deleted_reason"],
                fastf1_generated=record["fastf1_generated"],
                lap_start_time_ms=record["lap_start_time_ms"],
                lap_end_time_ms=record["lap_end_time_ms"],
            )


def ingest_race_laps(
    year: int,
    *,
    round_number: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Ingest lap data for one race round or all scheduled Race sessions in a season."""
    if year < 1950:
        raise ValueError("Season year must be 1950 or later.")
    if round_number is not None and round_number < 1:
        raise ValueError("Round number must be a positive integer.")

    setup_fastf1_cache()
    engine = get_engine()
    summary = {
        "sessions": 0,
        "source_rows": 0,
        "normalized_rows": 0,
        "excluded_missing_driver_code": 0,
        "excluded_invalid_lap_number": 0,
        "missing_lap_time_rows": 0,
    }
    try:
        scheduled_sessions = get_scheduled_race_sessions(engine, year, round_number)
        print(f"Season {year}: {len(scheduled_sessions)} Race session(s) queued.")
        for scheduled in scheduled_sessions:
            round_no = int(scheduled["round_number"])
            event_name = str(scheduled["event_name"])
            session_id = int(scheduled["session_id"])
            print(f"Loading R{round_no:02d}: {event_name} ...")

            try:
                laps = fetch_fastf1_race_laps(year, round_no)
                records, stats = normalize_laps(laps)
                print(
                    f"  Source rows: {stats['source_rows']}; "
                    f"upsertable rows: {stats['normalized_rows']}; "
                    f"missing lap times preserved: {stats['missing_lap_time_rows']}; "
                    f"excluded missing driver: {stats['excluded_missing_driver_code']}; "
                    f"excluded invalid lap number: {stats['excluded_invalid_lap_number']}."
                )
                if not dry_run:
                    _persist_laps(engine, session_id, records)

                summary["sessions"] += 1
                for key in (
                    "source_rows",
                    "normalized_rows",
                    "excluded_missing_driver_code",
                    "excluded_invalid_lap_number",
                    "missing_lap_time_rows",
                ):
                    summary[key] += stats[key]
            except Exception as exc:
                raise RuntimeError(
                    f"Race lap ingestion failed at season {year}, "
                    f"round {round_no} ({event_name}): {exc}"
                ) from exc
    finally:
        engine.dispose()

    action = "validated only; no lap rows written" if dry_run else "upsert complete"
    print(
        f"Season {year} lap ingestion {action}. "
        f"Sessions: {summary['sessions']}; source rows: {summary['source_rows']}; "
        f"upsertable rows: {summary['normalized_rows']}."
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest FastF1 Grand Prix race laps.")
    parser.add_argument("year", type=int, help="Season year, for example 2023")
    parser.add_argument(
        "--round",
        dest="round_number",
        type=int,
        help="Ingest one championship round first, for example --round 1.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate laps without writing lap or driver rows.",
    )
    args = parser.parse_args()

    try:
        ingest_race_laps(
            args.year,
            round_number=args.round_number,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        raise SystemExit(f"Race lap ingestion failed: {exc}") from exc


if __name__ == "__main__":
    main()
