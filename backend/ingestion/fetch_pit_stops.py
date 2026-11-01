"""Ingest historical Grand Prix pit-stop records from Jolpica's free API.

The loader uses Jolpica's Ergast-compatible historical pitstops and results
endpoints. Source duration is stored as reported in milliseconds. It is the
historical pit-stop duration (which may include pit entry/exit and, in unusual
cases, red-flag delay), not incremental time lost versus normal racing pace.

Run from the repository root:
    python -m backend.ingestion.fetch_pit_stops 2023 --round 1 --dry-run
    python -m backend.ingestion.fetch_pit_stops 2023 --round 1
    python -m backend.ingestion.fetch_pit_stops 2023
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

from ..database.connection import get_engine
from .fetch_race_laps import _clean_text, _positive_int
from .utils import insert_pit_stop, upsert_driver

JOLPICA_API_BASE = "https://api.jolpi.ca/ergast/f1"
REQUEST_INTERVAL_SECONDS = 2.1
REQUEST_TIMEOUT_SECONDS = 30
_last_request_monotonic = 0.0


def _get_jolpica_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch a JSON response from Jolpica with conservative request pacing."""
    global _last_request_monotonic

    elapsed = time.monotonic() - _last_request_monotonic
    if _last_request_monotonic and elapsed < REQUEST_INTERVAL_SECONDS:
        time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)

    query = urlencode(params or {})
    url = f"{JOLPICA_API_BASE}/{path.lstrip('/').rstrip('/')}.json"
    if query:
        url = f"{url}?{query}"
    request = Request(
        url,
        headers={"User-Agent": "F1-Pitstop-Strategy-Optimizer/0.1"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(
            f"Jolpica endpoint /{path} returned HTTP {exc.code}."
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach Jolpica endpoint /{path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Jolpica endpoint /{path} returned invalid JSON.") from exc

    _last_request_monotonic = time.monotonic()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Jolpica endpoint /{path} returned an unexpected response.")
    return payload


def _jolpica_race_payload(payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """Extract exactly one race from an Ergast-compatible response."""
    races = (
        payload.get("MRData", {})
        .get("RaceTable", {})
        .get("Races", [])
    )
    if len(races) != 1:
        raise RuntimeError(
            f"Jolpica endpoint /{endpoint} returned {len(races)} race records; "
            "expected exactly one."
        )
    return races[0]


def _duration_seconds(value: Any) -> float | None:
    """Parse seconds such as '25.885' or a minutes:seconds duration such as '40:55.302'."""
    if value is None:
        return None
    value_text = str(value).strip()
    if not value_text:
        return None

    try:
        if ":" not in value_text:
            seconds = float(value_text)
        else:
            parts = value_text.split(":")
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = minutes * 60 + float(parts[1])
            elif len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = hours * 3600 + minutes * 60 + float(parts[2])
            else:
                return None
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def normalize_pit_stops(
    pit_rows: list[dict[str, Any]],
    driver_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize historical pit stops and preserve source stop numbers when available.

    A pit stop lacking a duration or lap number remains in the normalized output
    with a NULL field and is counted. Rows lacking a usable driver mapping are
    skipped because they cannot be assigned the database foreign key.
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
        if duration_source is None:
            duration_source = pit.get("duration")
        duration_seconds = _duration_seconds(duration_source)
        duration_ms = None
        if duration_seconds is not None:
            duration_ms = int(round(duration_seconds * 1000))
        if duration_ms is None:
            stats["missing_duration_rows"] += 1

        source_stop_number = _positive_int(pit.get("stop"))
        source_date = _clean_text(pit.get("date"))
        if source_date is None:
            source_date = _clean_text(pit.get("time"))
        records.append({
            "driver_code": driver_code,
            "full_name": _clean_text(driver.get("full_name")) or driver_code,
            "nationality": _clean_text(driver.get("country_code")),
            "lap_number": lap_number,
            "duration_ms": duration_ms,
            "source_stop_number": source_stop_number,
            "source_date": source_date,
            "_original_index": original_index,
        })

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
    seen_identities: set[tuple[str, int]] = set()
    for record in records:
        code = record["driver_code"]
        stop_numbers[code] = stop_numbers.get(code, 0) + 1
        stop_number = record.pop("source_stop_number") or stop_numbers[code]
        identity = (code, stop_number)
        if identity in seen_identities:
            raise ValueError(
                f"Duplicate pit-stop identity in source: driver {code}, stop {stop_number}."
            )
        seen_identities.add(identity)
        record["stop_number"] = stop_number
        record.pop("_original_index", None)
        record.pop("source_date", None)

    stats["normalized_rows"] = len(records)
    return records, stats


def fetch_jolpica_pit_stops(
    year: int,
    round_number: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch pit-stop records and corresponding driver identities for one race."""
    pit_payload = _get_jolpica_json(
        f"{year}/{round_number}/pitstops",
        {"limit": 100},
    )
    results_payload = _get_jolpica_json(
        f"{year}/{round_number}/results",
        {"limit": 100},
    )

    pit_race = _jolpica_race_payload(
        pit_payload, f"{year}/{round_number}/pitstops"
    )
    results_race = _jolpica_race_payload(
        results_payload, f"{year}/{round_number}/results"
    )

    pit_rows = [
        {
            "driver_number": pit.get("driverId"),
            "lap_number": pit.get("lap"),
            "stop": pit.get("stop"),
            "time": pit.get("time"),
            "duration": pit.get("duration"),
        }
        for pit in pit_race.get("PitStops", [])
    ]
    driver_rows = []
    for result in results_race.get("Results", []):
        driver = result.get("Driver", {})
        driver_id = _clean_text(driver.get("driverId"))
        if not driver_id:
            continue
        given_name = _clean_text(driver.get("givenName")) or ""
        family_name = _clean_text(driver.get("familyName")) or ""
        driver_rows.append({
            "driver_number": driver_id,
            "name_acronym": _clean_text(driver.get("code")),
            "full_name": " ".join(part for part in (given_name, family_name) if part).strip(),
            "country_code": _clean_text(driver.get("nationality")),
        })

    if not pit_rows:
        race_name = pit_race.get("raceName") or f"{year} round {round_number}"
        raise RuntimeError(
            f"Jolpica returned no pit-stop records for {race_name}. "
            "Refusing to treat missing source data as a successfully ingested race."
        )
    return pit_rows, driver_rows


def _persist_pit_stops(engine, session_id: int, records: list[dict[str, Any]]) -> None:
    """Upsert all pit-stop visits for one race session in one transaction."""
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
    """Ingest pit-stop data for one Grand Prix round or all scheduled races."""
    if year < 2011:
        raise ValueError("Jolpica pit-stop history starts in 2011.")
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
        print(f"Season {year}: {len(scheduled_sessions)} Race session(s) queued.")
        for scheduled in scheduled_sessions:
            round_no = int(scheduled["round_number"])
            event_name = str(scheduled["event_name"])
            session_id = int(scheduled["session_id"])
            print(f"Loading R{round_no:02d}: {event_name} pit stops from Jolpica ...")

            try:
                pit_rows, driver_rows = fetch_jolpica_pit_stops(year, round_no)
                records, stats = normalize_pit_stops(pit_rows, driver_rows)
                print(
                    f"  Source pit stops: {stats['source_rows']}; "
                    f"upsertable stops: {stats['normalized_rows']}; "
                    f"missing lap numbers preserved: {stats['missing_lap_number_rows']}; "
                    f"missing pit durations preserved: {stats['missing_duration_rows']}; "
                    f"excluded unknown driver mappings: {stats['excluded_missing_driver_code']}."
                )
                if not records:
                    raise ValueError("No pit stops with usable driver identities remained after normalization.")
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
        f"Sessions: {summary['sessions']}; source stops: {summary['source_rows']}; "
        f"upsertable stops: {summary['normalized_rows']}."
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Jolpica historical Grand Prix pit stops.")
    parser.add_argument("year", type=int, help="Season year, 2011 or later")
    parser.add_argument(
        "--round",
        dest="round_number",
        type=int,
        help="Ingest one championship round first, for example --round 1.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate pit stops without writing driver or pit-stop rows.",
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
