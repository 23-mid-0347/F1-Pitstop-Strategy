"""Ingest Grand Prix race laps from OpenF1's free historical API.

OpenF1 provides historical data from 2023 onward without authentication.
The FastF1 package remains in use for schedule ingestion, but this lap loader
does not call FastF1's timing endpoints.

Run from the repository root after loading the schedule and initializing the DB:
    python -m backend.ingestion.fetch_race_laps 2023 --round 1 --dry-run
    python -m backend.ingestion.fetch_race_laps 2023 --round 1
    python -m backend.ingestion.fetch_race_laps 2023

The loader preserves lap records even when some optional fields are unavailable.
OpenF1 does not provide every FastF1 quality flag, so unsupported source fields
remain NULL rather than being guessed.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from sqlalchemy import text

from ..database.connection import get_engine
from .utils import insert_lap, upsert_driver

OPENF1_API_BASE = "https://api.openf1.org/v1"
# OpenF1's free historical tier allows 30 requests per minute.
REQUEST_INTERVAL_SECONDS = 2.1
REQUEST_TIMEOUT_SECONDS = 30
_last_request_monotonic = 0.0

_REQUIRED_LAP_COLUMNS = {"Driver", "LapNumber", "LapTime"}


def _get_openf1_json(endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch one OpenF1 endpoint while staying below its documented free-tier rate."""
    global _last_request_monotonic

    elapsed = time.monotonic() - _last_request_monotonic
    if _last_request_monotonic and elapsed < REQUEST_INTERVAL_SECONDS:
        time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)

    query = urlencode(params)
    request = Request(
        f"{OPENF1_API_BASE}/{endpoint}?{query}",
        headers={"User-Agent": "F1-Pitstop-Strategy-Optimizer/0.1"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError(
                "OpenF1 rate limit reached. Wait a minute, then retry the command."
            ) from exc
        raise RuntimeError(
            f"OpenF1 endpoint /{endpoint} returned HTTP {exc.code}."
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Could not reach OpenF1 endpoint /{endpoint}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenF1 endpoint /{endpoint} returned invalid JSON.") from exc

    _last_request_monotonic = time.monotonic()
    if not isinstance(payload, list):
        raise RuntimeError(f"OpenF1 endpoint /{endpoint} returned an unexpected response.")
    return payload


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
    return None if pd.isna(delta) else int(delta.value // 1_000_000)


def _time_flag(row: pd.Series, column_name: str) -> bool | None:
    """Return whether a FastF1 timing is present, or None when source lacks it."""
    if column_name not in row.index:
        return None
    return not _is_missing(row[column_name])


def _lap_flag(row: pd.Series, direct_column: str, timing_column: str) -> bool | None:
    """Prefer an explicit source boolean, otherwise use FastF1's timing marker."""
    if direct_column in row.index and not _is_missing(row[direct_column]):
        return _optional_bool(row[direct_column])
    return _time_flag(row, timing_column)


def normalize_laps(laps: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize OpenF1-backed laps while preserving unknown fields as nullable."""
    missing_columns = _REQUIRED_LAP_COLUMNS.difference(laps.columns)
    if missing_columns:
        raise ValueError(
            "OpenF1 lap data is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )
    if laps.empty:
        raise ValueError("OpenF1 returned an empty lap table for this race.")

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
                "OpenF1 returned duplicate lap identity "
                f"for driver {driver_code} lap {lap_number}."
            )
        seen_keys.add(key)

        lap_time_ms = _timedelta_ms(row.get("LapTime"))
        if lap_time_ms is None:
            stats["missing_lap_time_rows"] += 1

        records.append({
            "driver_code": driver_code,
            "full_name": _clean_text(row.get("DriverName")) or driver_code,
            "nationality": _clean_text(row.get("Nationality")),
            "lap_number": lap_number,
            "lap_time_ms": lap_time_ms,
            "tire_compound": _clean_text(row.get("Compound")),
            "stint_number": _positive_int(row.get("Stint")),
            "tyre_age": _optional_float(row.get("TyreLife")),
            "fresh_tyre": _optional_bool(row.get("FreshTyre")),
            "pit_in_lap": _lap_flag(row, "is_pit_in_lap", "PitInTime"),
            "pit_out_lap": _lap_flag(row, "is_pit_out_lap", "PitOutTime"),
            "track_status": _clean_text(row.get("TrackStatus")),
            "is_accurate": _optional_bool(row.get("IsAccurate")),
            "is_deleted": _optional_bool(row.get("Deleted")),
            "deleted_reason": _clean_text(row.get("DeletedReason")),
            "fastf1_generated": _optional_bool(row.get("FastF1Generated")),
            "lap_start_time_ms": _timedelta_ms(row.get("LapStartTime")),
            "lap_end_time_ms": _timedelta_ms(row.get("Time")),
        })

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
        scope = f"season {year}" if round_number is None else f"season {year}, round {round_number}"
        raise ValueError(
            f"No scheduled Race session found for {scope}. "
            "Run the schedule ingestion and schema initializer first."
        )
    return result


def fetch_openf1_race_sessions(year: int) -> list[dict[str, Any]]:
    """Return OpenF1 race sessions for the season, ordered by start date."""
    if year < 2023:
        raise ValueError("OpenF1's free historical dataset starts in 2023.")
    sessions = _get_openf1_json("sessions", {"year": year})
    races = [
        session for session in sessions
        if str(session.get("session_name", "")).strip().lower() == "race"
    ]
    races.sort(key=lambda session: str(session.get("date_start") or ""))
    if not races:
        raise ValueError(f"OpenF1 returned no Race sessions for season {year}.")
    return races


def _session_name_key(value: Any) -> str:
    """Normalize event names for matching the stored schedule to OpenF1."""
    normalized = "".join(character.lower() for character in str(value or "") if character.isalnum())
    for removable in ("grandprix", "formula1"):
        normalized = normalized.replace(removable, "")
    return normalized


def match_openf1_race_session(
    scheduled_event_name: str,
    openf1_sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match by meeting name; never guess a session when names do not agree."""
    target = _session_name_key(scheduled_event_name)
    matches = [
        session for session in openf1_sessions
        if target and target in {
            _session_name_key(session.get("meeting_name")),
            _session_name_key(session.get("meeting_official_name")),
        }
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"Multiple OpenF1 Race sessions match scheduled event {scheduled_event_name!r}."
        )
    available = ", ".join(str(session.get("meeting_name")) for session in openf1_sessions)
    raise ValueError(
        f"Could not match scheduled event {scheduled_event_name!r} to OpenF1. "
        f"OpenF1 race meetings: {available}"
    )


def _seconds_to_timedelta(value: Any):
    if _is_missing(value):
        return pd.NaT
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            return pd.NaT
        return pd.to_timedelta(seconds, unit="s")
    except (TypeError, ValueError, OverflowError):
        return pd.NaT


def fetch_openf1_race_laps(session_key: int | str) -> pd.DataFrame:
    """Fetch race laps and enrich compound/tyre age from OpenF1 stint records."""
    laps = _get_openf1_json("laps", {"session_key": session_key})
    stints = _get_openf1_json("stints", {"session_key": session_key})
    drivers = _get_openf1_json("drivers", {"session_key": session_key})

    drivers_by_number = {
        str(driver.get("driver_number")): driver
        for driver in drivers
        if driver.get("driver_number") is not None
    }
    stints_by_driver: dict[str, list[dict[str, Any]]] = {}
    for stint in stints:
        number = str(stint.get("driver_number"))
        stints_by_driver.setdefault(number, []).append(stint)
    for driver_stints in stints_by_driver.values():
        driver_stints.sort(key=lambda item: _positive_int(item.get("stint_number")) or 0)

    normalized_rows: list[dict[str, Any]] = []
    for lap in laps:
        driver_number = str(lap.get("driver_number"))
        driver = drivers_by_number.get(driver_number, {})
        lap_number = _positive_int(lap.get("lap_number"))
        matching_stint = None
        if lap_number is not None:
            for stint in stints_by_driver.get(driver_number, []):
                lap_start = _positive_int(stint.get("lap_start"))
                lap_end = _positive_int(stint.get("lap_end"))
                if lap_start is not None and lap_end is not None and lap_start <= lap_number <= lap_end:
                    matching_stint = stint
                    break

        tyre_age = None
        stint_number = None
        compound = None
        if matching_stint is not None and lap_number is not None:
            stint_start = _positive_int(matching_stint.get("lap_start"))
            age_at_start = _optional_float(matching_stint.get("tyre_age_at_start"))
            stint_number = _positive_int(matching_stint.get("stint_number"))
            compound = _clean_text(matching_stint.get("compound"))
            if stint_start is not None and age_at_start is not None:
                tyre_age = age_at_start + (lap_number - stint_start)

        normalized_rows.append({
            "Driver": _clean_text(driver.get("name_acronym")) or driver_number,
            "DriverName": _clean_text(driver.get("full_name")),
            "Nationality": _clean_text(driver.get("country_code")),
            "LapNumber": lap.get("lap_number"),
            "LapTime": _seconds_to_timedelta(lap.get("lap_duration")),
            "Compound": compound,
            "Stint": stint_number,
            "TyreLife": tyre_age,
            "is_pit_out_lap": lap.get("is_pit_out_lap"),
        })

    if not normalized_rows:
        raise ValueError(f"OpenF1 returned no lap records for session_key={session_key}.")
    return pd.DataFrame(normalized_rows)


def _persist_laps(engine, session_id: int, records: list[dict[str, Any]]) -> None:
    """Upsert one session in a single transaction."""
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
    """Ingest OpenF1 lap data for one race round or all scheduled races in a season."""
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
        "excluded_invalid_lap_number": 0,
        "missing_lap_time_rows": 0,
    }
    try:
        scheduled_sessions = get_scheduled_race_sessions(engine, year, round_number)
        openf1_sessions = fetch_openf1_race_sessions(year)
        print(f"Season {year}: {len(scheduled_sessions)} Race session(s) queued.")
        for scheduled in scheduled_sessions:
            round_no = int(scheduled["round_number"])
            event_name = str(scheduled["event_name"])
            session_id = int(scheduled["session_id"])
            print(f"Loading R{round_no:02d}: {event_name} from OpenF1 ...")

            try:
                source_session = match_openf1_race_session(event_name, openf1_sessions)
                session_key = source_session.get("session_key")
                if session_key is None:
                    raise ValueError("matched OpenF1 session has no session_key")

                laps = fetch_openf1_race_laps(session_key)
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
    parser = argparse.ArgumentParser(description="Ingest OpenF1 Grand Prix race laps.")
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
