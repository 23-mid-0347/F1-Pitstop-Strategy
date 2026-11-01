"""Ingest a Formula 1 season schedule into PostgreSQL.

Run from the repository root:
    python -m backend.ingestion.fetch_schedule 2023
    python -m backend.ingestion.fetch_schedule 2023 --dry-run

The initial schedule does not reliably provide official circuit names. This
loader stores the source's Country and Location on each event and leaves
events.circuit_id NULL until circuit identity can be sourced reliably.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from typing import Any

import fastf1
import pandas as pd

from ..database.connection import get_engine
from .utils import setup_fastf1_cache, upsert_event, upsert_season, upsert_session


_REQUIRED_COLUMNS = {"RoundNumber", "Country", "Location", "EventName", "EventDate"}


def _is_missing(value: Any) -> bool:
    """Return True for None, pandas NA/NaT, or NaN scalar values."""
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


def _to_date(value: Any) -> date | None:
    if _is_missing(value):
        return None
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _to_db_timestamp(value: Any) -> datetime | None:
    """Convert a schedule timestamp to a naive UTC datetime for TIMESTAMP columns."""
    if _is_missing(value):
        return None

    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp.to_pydatetime()


def _session_date(row: pd.Series, session_number: int) -> datetime | None:
    # FastF1 documents SessionNDateUtc as a timezone-naive UTC timestamp.
    # Prefer it; if it is unavailable, normalize the local timestamp to UTC.
    utc_value = row.get(f"Session{session_number}DateUtc")
    if not _is_missing(utc_value):
        return _to_db_timestamp(utc_value)
    return _to_db_timestamp(row.get(f"Session{session_number}Date"))


def normalize_schedule(schedule: pd.DataFrame) -> tuple[list[dict[str, Any]], int]:
    """Validate and normalize FastF1 schedule rows.

    Returns (event records, number of round-zero/testing rows skipped).
    """
    missing_columns = _REQUIRED_COLUMNS.difference(schedule.columns)
    if missing_columns:
        raise ValueError(
            "FastF1 schedule is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )
    if schedule.empty:
        raise ValueError("FastF1 returned an empty event schedule.")

    events: list[dict[str, Any]] = []
    seen_rounds: set[int] = set()
    skipped_testing_rows = 0

    for _, row in schedule.iterrows():
        round_value = row.get("RoundNumber")
        if _is_missing(round_value):
            raise ValueError("A schedule row is missing RoundNumber.")

        try:
            round_number = int(round_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid RoundNumber: {round_value!r}") from exc

        # Round zero is used for testing events; it is not a championship round.
        if round_number <= 0:
            skipped_testing_rows += 1
            continue
        if round_number in seen_rounds:
            raise ValueError(f"Duplicate championship round in schedule: {round_number}")

        event_name = _clean_text(row.get("EventName"))
        if event_name is None:
            raise ValueError(f"Round {round_number} is missing EventName.")

        sessions: list[dict[str, Any]] = []
        for session_number in range(1, 6):
            session_name = _clean_text(row.get(f"Session{session_number}"))
            if session_name is None:
                continue
            sessions.append(
                {
                    "session_type": session_name,
                    "session_date": _session_date(row, session_number),
                }
            )
        if not sessions:
            raise ValueError(f"Round {round_number} ({event_name}) has no session names.")

        events.append(
            {
                "round_number": round_number,
                "event_name": event_name,
                "event_date": _to_date(row.get("EventDate")),
                "country": _clean_text(row.get("Country")),
                "location": _clean_text(row.get("Location")),
                "official_event_name": _clean_text(row.get("OfficialEventName")),
                "event_format": _clean_text(row.get("EventFormat")),
                "sessions": sessions,
            }
        )
        seen_rounds.add(round_number)

    if not events:
        raise ValueError("No championship events remained after schedule validation.")

    events.sort(key=lambda event: event["round_number"])
    return events, skipped_testing_rows


def ingest_schedule(year: int, *, dry_run: bool = False) -> dict[str, int]:
    """Fetch and optionally upsert one season's schedule."""
    if year < 1950:
        raise ValueError("Season year must be 1950 or later.")

    setup_fastf1_cache()
    schedule = fastf1.get_event_schedule(year, include_testing=False)
    events, skipped_testing_rows = normalize_schedule(schedule)
    session_count = sum(len(event["sessions"]) for event in events)

    print(
        f"Season {year}: {len(events)} events, {session_count} sessions; "
        f"skipped {skipped_testing_rows} round-zero/testing rows."
    )
    for event in events:
        country = event["country"] or "country unknown"
        location = event["location"] or "location unknown"
        print(
            f"  R{event['round_number']:02d}: {event['event_name']} "
            f"— {location}, {country} ({len(event['sessions'])} sessions)"
        )

    if dry_run:
        print("Dry run complete; no database changes were made.")
        return {
            "events": len(events),
            "sessions": session_count,
            "skipped_testing_rows": skipped_testing_rows,
        }

    engine = get_engine()
    try:
        with engine.begin() as conn:
            season_id = upsert_season(conn, year)
            for event in events:
                event_id = upsert_event(
                    conn,
                    season_id=season_id,
                    round_no=event["round_number"],
                    event_name=event["event_name"],
                    circuit_id=None,
                    event_date=event["event_date"],
                    country=event["country"],
                    location=event["location"],
                    official_event_name=event["official_event_name"],
                    event_format=event["event_format"],
                )
                for session in event["sessions"]:
                    upsert_session(
                        conn,
                        event_id=event_id,
                        session_type=session["session_type"],
                        session_date=session["session_date"],
                    )
    finally:
        engine.dispose()

    print(
        f"Database upsert complete for season {year}: "
        f"{len(events)} events and {session_count} sessions attempted."
    )
    return {
        "events": len(events),
        "sessions": session_count,
        "skipped_testing_rows": skipped_testing_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a Formula 1 season schedule.")
    parser.add_argument("year", type=int, help="Season year, for example 2023")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate the schedule without writing to PostgreSQL.",
    )
    args = parser.parse_args()

    try:
        ingest_schedule(args.year, dry_run=args.dry_run)
    except Exception as exc:
        raise SystemExit(f"Schedule ingestion failed: {exc}") from exc


if __name__ == "__main__":
    main()
