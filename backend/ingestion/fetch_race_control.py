"""Ingest Safety Car, VSC, and red-flag periods from OpenF1 race control.

The source's race-control lap_number is preferred when supplied. If absent, a
timestamp is assigned to the nearest median lap start across observed drivers.
This defines an explicit mid-lap boundary: the midpoint between two adjacent
median lap starts belongs to the earlier lap; timestamps after it belong to the
next lap. This is an approximation because OpenF1 lap start timestamps are
documented as approximate.

Period end messages are paired by type. If a period remains open when the race's
observed lap records end, the loader closes it at the observed race horizon and
marks end_inferred=true. These inferred boundaries must be reported as such.

Run from the repository root:
    python -m backend.ingestion.fetch_race_control 2023 --round 1 --dry-run
    python -m backend.ingestion.fetch_race_control 2023 --round 1
    python -m backend.ingestion.fetch_race_control 2023
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database.connection import get_engine
from .fetch_race_laps import (
    _get_openf1_json,
    _positive_int,
    fetch_openf1_race_sessions,
    get_scheduled_race_sessions,
    match_openf1_race_session,
)
from .utils import insert_safety_car_period

_TRANSITION_TERMS = (
    "VIRTUAL SAFETY CAR DEPLOYED",
    "VSC DEPLOYED",
    "VIRTUAL SAFETY CAR ENDING",
    "VSC ENDING",
    "SAFETY CAR DEPLOYED",
    "SAFETY CAR IN THIS LAP",
    "RED FLAG",
    "SESSION RESTARTED",
)


def parse_utc_timestamp(value: Any) -> datetime | None:
    """Parse ISO-8601 and return UTC as a naive datetime for PostgreSQL TIMESTAMP."""
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            value_text = str(value).strip()
            if not value_text:
                return None
            if value_text.endswith("Z"):
                value_text = value_text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(value_text)
    except (TypeError, ValueError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _median_datetime(values: list[datetime]) -> datetime:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return ordered[middle - 1] + (ordered[middle] - ordered[middle - 1]) / 2


def build_lap_timeline(
    lap_rows: list[dict[str, Any]],
) -> tuple[list[tuple[datetime, int]], int, datetime]:
    """Build median lap-start points and race horizon from raw OpenF1 lap rows."""
    starts_by_lap: dict[int, list[datetime]] = {}
    lap_end_times: list[tuple[int, datetime]] = []

    for row in lap_rows:
        lap_number = _positive_int(row.get("lap_number"))
        start_time = parse_utc_timestamp(row.get("date_start"))
        if lap_number is None or start_time is None:
            continue
        starts_by_lap.setdefault(lap_number, []).append(start_time)

        duration = row.get("lap_duration")
        try:
            duration_seconds = float(duration)
        except (TypeError, ValueError, OverflowError):
            duration_seconds = None
        if duration_seconds is not None and duration_seconds >= 0:
            lap_end_times.append(
                (lap_number, start_time + timedelta(seconds=duration_seconds))
            )

    if not starts_by_lap:
        raise ValueError(
            "No usable lap_number/date_start pairs were found to map race-control timestamps."
        )

    timeline = sorted(
        (_median_datetime(starts), lap_number)
        for lap_number, starts in starts_by_lap.items()
    )
    max_lap = max(starts_by_lap)

    horizon_candidates = [
        end_time for lap_number, end_time in lap_end_times if lap_number == max_lap
    ]
    if horizon_candidates:
        race_horizon = max(horizon_candidates)
    else:
        race_horizon = max(start_time for start_time, _ in timeline)

    return timeline, max_lap, race_horizon


def map_event_to_lap(
    event: dict[str, Any],
    timeline: list[tuple[datetime, int]],
    max_lap: int,
) -> tuple[int, str]:
    """Map an event to a lap, preferring source lap_number to timestamp fallback.

    The timestamp fallback chooses the nearest median lap start. A timestamp
    exactly halfway between consecutive median starts maps to the earlier lap.
    """
    source_lap = _positive_int(event.get("lap_number"))
    if source_lap is not None and source_lap <= max_lap:
        return source_lap, "source_lap_number"

    event_time = parse_utc_timestamp(event.get("date"))
    if event_time is None:
        raise ValueError("Race-control event has neither a usable lap number nor timestamp.")

    nearest_time, nearest_lap = min(
        timeline,
        key=lambda point: (abs((point[0] - event_time).total_seconds()), point[1]),
    )
    _ = nearest_time
    return nearest_lap, "nearest_median_lap_start"


def _classify_transition(
    event: dict[str, Any],
    active_types: set[str],
) -> tuple[str, str] | None:
    """Classify the limited race-control messages needed for period construction."""
    message = str(event.get("message") or "").upper().strip()
    flag = str(event.get("flag") or "").upper().strip()
    category = str(event.get("category") or "").upper().strip()

    if "VIRTUAL SAFETY CAR ENDING" in message or "VSC ENDING" in message:
        return "VSC", "end"
    if "VIRTUAL SAFETY CAR DEPLOYED" in message or "VSC DEPLOYED" in message:
        return "VSC", "start"

    if "SAFETY CAR IN THIS LAP" in message:
        return "SC", "end"
    if "SAFETY CAR DEPLOYED" in message and "VIRTUAL" not in message:
        return "SC", "start"

    if flag == "RED" or "RED FLAG" in message:
        return "RedFlag", "start"
    if "RedFlag" in active_types and (
        flag in {"GREEN", "CLEAR"}
        or "SESSION RESTARTED" in message
        or "GREEN FLAG" in message
    ):
        return "RedFlag", "end"

    # A SafetyCar category without a known transition is intentionally ignored.
    _ = category
    return None


def normalize_race_control(
    race_control_rows: list[dict[str, Any]],
    lap_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Pair race-control messages into lap-level neutralisation periods."""
    if not race_control_rows:
        raise ValueError(
            "OpenF1 returned no race-control messages; refusing to infer an event-free race."
        )

    timeline, max_lap, race_horizon = build_lap_timeline(lap_rows)
    stats = {
        "source_rows": len(race_control_rows),
        "recognized_transitions": 0,
        "periods": 0,
        "excluded_invalid_timestamp_rows": 0,
        "approximate_lap_mappings": 0,
        "unmatched_end_events": 0,
        "duplicate_start_events": 0,
        "inferred_end_periods": 0,
        "closed_at_race_horizon": 0,
    }

    dated_rows: list[tuple[datetime, int, dict[str, Any]]] = []
    for original_index, row in enumerate(race_control_rows):
        event_time = parse_utc_timestamp(row.get("date"))
        # Count invalid timestamps only for messages likely to affect this model.
        message = str(row.get("message") or "").upper()
        flag = str(row.get("flag") or "").upper()
        if event_time is None:
            if flag in {"RED", "GREEN", "CLEAR"} or any(
                term in message for term in _TRANSITION_TERMS
            ):
                stats["excluded_invalid_timestamp_rows"] += 1
            continue
        dated_rows.append((event_time, original_index, row))
    dated_rows.sort(key=lambda item: (item[0], item[1]))

    active: dict[str, dict[str, Any]] = {}
    periods: list[dict[str, Any]] = []

    def close_period(
        period_type: str,
        end_time: datetime,
        end_lap: int,
        *,
        inferred: bool,
        end_lap_method: str,
    ) -> None:
        opened = active.pop(period_type)
        safe_end_time = max(opened["start_time"], end_time)
        safe_end_lap = max(opened["start_lap"], min(end_lap, max_lap))
        periods.append({
            "start_time": opened["start_time"],
            "end_time": safe_end_time,
            "start_lap": opened["start_lap"],
            "end_lap": safe_end_lap,
            "period_type": period_type,
            "end_inferred": inferred,
            "start_lap_method": opened["start_lap_method"],
            "end_lap_method": end_lap_method,
        })
        if inferred:
            stats["inferred_end_periods"] += 1

    for event_time, _index, event in dated_rows:
        transition = _classify_transition(event, set(active))
        if transition is None:
            continue
        period_type, action = transition
        try:
            event_lap, lap_method = map_event_to_lap(event, timeline, max_lap)
        except ValueError:
            stats["excluded_invalid_timestamp_rows"] += 1
            continue
        if lap_method != "source_lap_number":
            stats["approximate_lap_mappings"] += 1
        stats["recognized_transitions"] += 1

        if action == "end":
            if period_type not in active:
                stats["unmatched_end_events"] += 1
                continue
            close_period(
                period_type,
                event_time,
                event_lap,
                inferred=False,
                end_lap_method=lap_method,
            )
            continue

        # A new neutralisation supersedes any other active neutralisation.
        # This prevents overlapping time intervals at the chosen time resolution.
        for other_type in tuple(active):
            if other_type != period_type:
                close_period(
                    other_type,
                    event_time,
                    event_lap,
                    inferred=True,
                    end_lap_method=lap_method,
                )

        if period_type in active:
            stats["duplicate_start_events"] += 1
            continue

        active[period_type] = {
            "start_time": event_time,
            "start_lap": event_lap,
            "start_lap_method": lap_method,
        }

    for period_type in tuple(active):
        close_period(
            period_type,
            race_horizon,
            max_lap,
            inferred=True,
            end_lap_method="race_horizon",
        )
        stats["closed_at_race_horizon"] += 1

    periods.sort(key=lambda period: (period["start_time"], period["period_type"]))
    stats["periods"] = len(periods)
    return periods, stats


def fetch_openf1_race_control(session_key: int | str) -> list[dict[str, Any]]:
    """Fetch race-control messages for a specific race session."""
    rows = _get_openf1_json("race_control", {"session_key": session_key})
    if not rows:
        raise ValueError(
            f"OpenF1 returned no race-control messages for session_key={session_key}."
        )
    return rows


def fetch_openf1_lap_boundaries(session_key: int | str) -> list[dict[str, Any]]:
    """Fetch raw lap start timestamps used only for timestamp-to-lap mapping."""
    rows = _get_openf1_json("laps", {"session_key": session_key})
    if not rows:
        raise ValueError(
            f"OpenF1 returned no lap records for session_key={session_key}; "
            "race-control periods cannot be mapped safely."
        )
    return rows


def _persist_periods(engine, session_id: int, periods: list[dict[str, Any]]) -> None:
    """Upsert constructed periods in a transaction."""
    with engine.begin() as connection:
        for period in periods:
            insert_safety_car_period(
                connection,
                session_id=session_id,
                start_time=period["start_time"],
                end_time=period["end_time"],
                start_lap=period["start_lap"],
                end_lap=period["end_lap"],
                period_type=period["period_type"],
                end_inferred=period["end_inferred"],
                start_lap_method=period["start_lap_method"],
                end_lap_method=period["end_lap_method"],
            )


def ingest_race_control(
    year: int,
    *,
    round_number: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Ingest SC/VSC/red-flag periods for one or all scheduled Grand Prix races."""
    if year < 2023:
        raise ValueError("OpenF1's free historical dataset starts in 2023.")
    if round_number is not None and round_number < 1:
        raise ValueError("Round number must be a positive integer.")

    engine = get_engine()
    summary = {
        "sessions": 0,
        "source_rows": 0,
        "recognized_transitions": 0,
        "periods": 0,
        "excluded_invalid_timestamp_rows": 0,
        "approximate_lap_mappings": 0,
        "unmatched_end_events": 0,
        "duplicate_start_events": 0,
        "inferred_end_periods": 0,
        "closed_at_race_horizon": 0,
    }
    try:
        scheduled_sessions = get_scheduled_race_sessions(engine, year, round_number)
        openf1_sessions = fetch_openf1_race_sessions(year)
        print(f"Season {year}: {len(scheduled_sessions)} Race session(s) queued.")

        for scheduled in scheduled_sessions:
            round_no = int(scheduled["round_number"])
            event_name = str(scheduled["event_name"])
            session_id = int(scheduled["session_id"])
            print(f"Loading R{round_no:02d}: {event_name} race-control periods from OpenF1 ...")

            try:
                source_session = match_openf1_race_session(event_name, openf1_sessions)
                session_key = source_session.get("session_key")
                if session_key is None:
                    raise ValueError("matched OpenF1 session has no session_key")

                race_control_rows = fetch_openf1_race_control(session_key)
                lap_rows = fetch_openf1_lap_boundaries(session_key)
                periods, stats = normalize_race_control(race_control_rows, lap_rows)

                print(
                    f"  Source messages: {stats['source_rows']}; "
                    f"recognized transitions: {stats['recognized_transitions']}; "
                    f"periods: {stats['periods']}; "
                    f"approximate lap mappings: {stats['approximate_lap_mappings']}; "
                    f"unmatched ends: {stats['unmatched_end_events']}; "
                    f"inferred ends: {stats['inferred_end_periods']} "
                    f"(at race horizon: {stats['closed_at_race_horizon']})."
                )

                if not dry_run and periods:
                    _persist_periods(engine, session_id, periods)

                summary["sessions"] += 1
                for key in (
                    "source_rows",
                    "recognized_transitions",
                    "periods",
                    "excluded_invalid_timestamp_rows",
                    "approximate_lap_mappings",
                    "unmatched_end_events",
                    "duplicate_start_events",
                    "inferred_end_periods",
                    "closed_at_race_horizon",
                ):
                    summary[key] += stats[key]
            except Exception as exc:
                raise RuntimeError(
                    f"Race-control ingestion failed at season {year}, "
                    f"round {round_no} ({event_name}): {exc}"
                ) from exc
    finally:
        engine.dispose()

    action = (
        "validated only; no period rows written" if dry_run else "upsert complete"
    )
    print(
        f"Season {year} race-control ingestion {action}. "
        f"Sessions: {summary['sessions']}; source messages: {summary['source_rows']}; "
        f"periods: {summary['periods']}."
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest OpenF1 Safety Car, VSC, and red-flag periods."
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
        help="Fetch and validate periods without writing period rows.",
    )
    args = parser.parse_args()

    try:
        ingest_race_control(
            args.year,
            round_number=args.round_number,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        raise SystemExit(f"Race-control ingestion failed: {exc}") from exc


if __name__ == "__main__":
    main()
