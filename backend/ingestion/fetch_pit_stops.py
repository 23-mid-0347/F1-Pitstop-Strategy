"""Ingest Grand Prix pit-lane visits from OpenF1's free historical API.

The database's duration_ms field stores the source's time in the pit lane
(lane_duration, or legacy pit_duration), not incremental time lost versus
normal racing pace. Keep that distinction explicit for the strategy model.

Run from the repository root:
    python -m backend.ingestion.fetch_pit_stops 2023 --round 1 --dry-run
    python -m backend.ingestion.fetch_pit_stops 2023 --round 1
    python -m backend.ingestion.fetch_pit_stops 2023
"""
from __future__ import annotations

import argparse
import math
from typing import Any

from ..database.connection import get_engine
from .fetch_race_laps import (
    _clean_text,
    _get_openf1_json,
    _optional_float,
    _positive_int,
    fetch_openf1_race_sessions,
    get_scheduled_race_sessions,
    match_openf1_race_session,
)
from .utils import insert_pit_stop, upsert_driver


def normalize_pit_stops(
    pit_rows: list[dict[str, Any]],
    driver_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize source pit visits and assign stable per-driver stop numbers.

    Stop numbers are assigned in chronological order per driver, with lap
    number and original row order as deterministic fallbacks when dates are
    absent. A record lacking a duration or lap number is retained as nullable
    data; records without a driver identity cannot be assigned a foreign key.
    """
    drivers_by_number = {
        str(driver.get("driver_number")): driver
        for driver in driver_rows
        if driver.get("driver_number") is not None
    }
    stats = {
        "source_rows": len(pit_rows),
        "normalized_rows": 0,
        "excluded_missing_driver_code": 0,
        "missing_lap_number_rows": 0,
        "missing_duration_rows": 0,
    }
    records: list[dict[str, Any]] = []

    for original_index, pit in enumerate(pit_rows):
        driver_number = pit.get("driver_number")
        driver = drivers_by_number.get(str(driver_number), {})
        driver_code = _clean_text(driver.get("name_acronym"))
        if driver_code is None:
            stats["excluded_missing_driver_code"] += 1
            continue

        lap_number = _positive_int(pit.get("lap_number"))
        if lap_number is None:
            stats["missing_lap_number_rows"] += 1

        duration_source = pit.get("lane_duration")
        if duration_source is None:
            duration_source = pit.get("pit_duration")
        duration_seconds = _optional_float(duration_source)
        duration_ms = None
        if duration_seconds is not None and duration_seconds >= 0 and math.isfinite(duration_seconds):
            duration_ms = int(round(duration_seconds * 1000))
        if duration_ms is None:
            stats["missing_duration_rows"] += 1

        source_date = _clean_text(pit.get("date"))
        records.append({
            "driver_code": driver_code,
            "full_name": _clean_text(driver.get("full_name")) or driver_code,
            "nationality": _clean_text(driver.get("country_code")),
            "lap_number": lap_number,
            "duration_ms": duration_ms,
            "source_date": source_date,
            "_original_index": original_index,
        })

    # ISO timestamps sort chronologically as strings. If timestamps are absent,
    # use lap number, then source order, to keep stop numbering deterministic.
    records.sort(
        key=lambda record: (
            record["driver_code"],
            record["source_date"] is None,
            record["source_date"] or "",
            record["lap_number"] if record["lap_number"] is not None else 0,
            record["_original_index"],
        )
    )
    stop_numbers: dict[str, int] = {}
    for record in records:
        code = record["driver_code"]
        stop_numbers[code] = stop_numbers.get(code, 0) + 1
        record["stop_number"] = stop_numbers[code]
        record.pop("_original_index", None)
        record.pop("source_date", None)

    stats["normalized_rows"] = len(records)
    return records, stats


def fetch_openf1_pit_stops(session_key: int | str) -> list[dict[str, Any]]:
    """Fetch pit-lane visit records for one OpenF1 race session."""
    return _get_openf1_json("pit", {"session_key": session_key})


def fetch_openf1_drivers(session_key: int | str) -> list[dict[str, Any]]:
    """Fetch driver number-to-code/name mappings for one OpenF1 session."""
    return _get_openf1_json("drivers", {"session_key": session_key})


def _persist_pit_stops(engine, session_id: int, records: list[dict[str, Any]]) -> None:
    """Upsert all pit-lane visits for one race session in one transaction."""
    with engine.begin() as connection:
        driver_ids: dict[str, int] = {}
        for record in records:
            code = record["driver_code"]
            if code not in driver_ids:
                driver_ids[code] = upsert_driver(
                    connection,
                    code=code,
                    full_name=record["full_name"],
                    nationality=record["nationality"],
                )
            insert_pit_stop(
                connection,
                session_id=session_id,
                driver_id=driver_ids[code],
                stop_number=record["stop_number"],
                lap=record["lap_number"],
                duration_ms=record["duration_ms"],
            )


def ingest_pit_stops(
    year: int,
    *,
    round_number: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Ingest pit-lane visits for one Grand Prix round or all scheduled races."""
    if year < 2023:
        raise ValueError("OpenF1's free historical dataset starts in 2023.")
    if round_number is not None and round_number < 1:
        raise ValueError("Round number must be a positive integer.")

    engine = get_engine()
    summary = {
        "sessions": 0,
        "source_rows": 0,
        "normalized_rows": 0,
        "excluded_missing_driver_code": 0,
        "missing_lap_number_rows": 0,
        "missing_duration_rows": 0,
    }
    try:
        scheduled_sessions = get_scheduled_race_sessions(engine, year, round_number)
        openf1_sessions = fetch_openf1_race_sessions(year)
        print(f"Season {year}: {len(scheduled_sessions)} Race session(s) queued.")
        for scheduled in scheduled_sessions:
            round_no = int(scheduled["round_number"])
            event_name = str(scheduled["event_name"])
            session_id = int(scheduled["session_id"])
            print(f"Loading R{round_no:02d}: {event_name} pit stops from OpenF1 ...")

            try:
                source_session = match_openf1_race_session(event_name, openf1_sessions)
                session_key = source_session.get("session_key")
                if session_key is None:
                    raise ValueError("matched OpenF1 session has no session_key")

                pit_rows = fetch_openf1_pit_stops(session_key)
                driver_rows = fetch_openf1_drivers(session_key)
                records, stats = normalize_pit_stops(pit_rows, driver_rows)
                print(
                    f"  Source pit visits: {stats['source_rows']}; "
                    f"upsertable visits: {stats['normalized_rows']}; "
                    f"missing lap numbers preserved: {stats['missing_lap_number_rows']}; "
                    f"missing pit-lane durations preserved: {stats['missing_duration_rows']}; "
                    f"excluded unknown driver mappings: {stats['excluded_missing_driver_code']}."
                )
                if not dry_run:
                    _persist_pit_stops(engine, session_id, records)

                summary["sessions"] += 1
                for key in (
                    "source_rows",
                    "normalized_rows",
                    "excluded_missing_driver_code",
                    "missing_lap_number_rows",
                    "missing_duration_rows",
                ):
                    summary[key] += stats[key]
            except Exception as exc:
                raise RuntimeError(
                    f"Pit-stop ingestion failed at season {year}, "
                    f"round {round_no} ({event_name}): {exc}"
                ) from exc
    finally:
        engine.dispose()

    action = "validated only; no pit-stop rows written" if dry_run else "upsert complete"
    print(
        f"Season {year} pit-stop ingestion {action}. "
        f"Sessions: {summary['sessions']}; source visits: {summary['source_rows']}; "
        f"upsertable visits: {summary['normalized_rows']}."
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest OpenF1 Grand Prix pit-lane visits.")
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
        help="Fetch and validate pit visits without writing driver or pit-stop rows.",
    )
    args = parser.parse_args()

    try:
        ingest_pit_stops(
            args.year,
            round_number=args.round_number,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        raise SystemExit(f"Pit-stop ingestion failed: {exc}") from exc


if __name__ == "__main__":
    main()
