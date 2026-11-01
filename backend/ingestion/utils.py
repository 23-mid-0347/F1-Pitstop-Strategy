import os

import fastf1
import pandas as pd
from sqlalchemy import text

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


def setup_fastf1_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)
    fastf1.Cache.configure(cache_dir=CACHE_DIR)


def upsert_season(conn, year: int) -> int:
    result = conn.execute(
        text("INSERT INTO seasons (year) VALUES (:year) ON CONFLICT (year) DO UPDATE SET year = EXCLUDED.year RETURNING season_id"),
        {"year": year},
    )
    return result.scalar_one()


def upsert_circuit(conn, name: str, location: str, country: str) -> int:
    result = conn.execute(
        text("""
            INSERT INTO circuits (name, location, country) VALUES (:name, :location, :country)
            ON CONFLICT (name) DO UPDATE SET location = EXCLUDED.location, country = EXCLUDED.country
            RETURNING circuit_id
        """),
        {"name": name, "location": location, "country": country},
    )
    return result.scalar_one()


def upsert_event(
    conn,
    season_id: int,
    round_no: int,
    event_name: str,
    circuit_id: int | None,
    event_date,
    country: str | None = None,
    location: str | None = None,
    official_event_name: str | None = None,
    event_format: str | None = None,
) -> int:
    """Insert or refresh an event while preserving fields absent from a source update."""
    result = conn.execute(
        text("""
            INSERT INTO events (
                season_id, round, event_name, circuit_id, event_date,
                country, location, official_event_name, event_format
            )
            VALUES (
                :season_id, :round_no, :event_name, :circuit_id, :event_date,
                :country, :location, :official_event_name, :event_format
            )
            ON CONFLICT (season_id, round) DO UPDATE SET
                event_name = EXCLUDED.event_name,
                circuit_id = COALESCE(EXCLUDED.circuit_id, events.circuit_id),
                event_date = COALESCE(EXCLUDED.event_date, events.event_date),
                country = COALESCE(EXCLUDED.country, events.country),
                location = COALESCE(EXCLUDED.location, events.location),
                official_event_name = COALESCE(EXCLUDED.official_event_name, events.official_event_name),
                event_format = COALESCE(EXCLUDED.event_format, events.event_format)
            RETURNING event_id
        """),
        {
            "season_id": season_id,
            "round_no": round_no,
            "event_name": event_name,
            "circuit_id": circuit_id,
            "event_date": event_date,
            "country": country,
            "location": location,
            "official_event_name": official_event_name,
            "event_format": event_format,
        },
    )
    return result.scalar_one()


def upsert_session(conn, event_id: int, session_type: str, session_date) -> int:
    result = conn.execute(
        text("""
            INSERT INTO sessions (event_id, session_type, session_date)
            VALUES (:event_id, :session_type, :session_date)
            ON CONFLICT (event_id, session_type) DO UPDATE
                SET session_date = COALESCE(EXCLUDED.session_date, sessions.session_date)
            RETURNING session_id
        """),
        {"event_id": event_id, "session_type": session_type, "session_date": session_date},
    )
    return result.scalar_one()


def get_season_id(conn, year: int):
    row = conn.execute(text("SELECT season_id FROM seasons WHERE year = :year"), {"year": year}).first()
    return row[0] if row else None


def get_event_id(conn, season_id: int, round_no: int):
    row = conn.execute(
        text("SELECT event_id FROM events WHERE season_id = :season_id AND round = :round"),
        {"season_id": season_id, "round": round_no},
    ).first()
    return row[0] if row else None


def upsert_driver(conn, code: str, full_name: str, nationality: str | None) -> int:
    result = conn.execute(
        text("""
            INSERT INTO drivers (driver_code, full_name, nationality) VALUES (:code, :full_name, :nationality)
            ON CONFLICT (driver_code) DO UPDATE SET
                full_name = EXCLUDED.full_name, nationality = EXCLUDED.nationality
            RETURNING driver_id
        """),
        {"code": code, "full_name": full_name, "nationality": nationality},
    )
    return result.scalar_one()


def insert_lap(
    conn,
    session_id,
    driver_id,
    lap_number,
    lap_time_ms,
    tire_compound,
    stint_number=None,
    tyre_age=None,
    fresh_tyre=None,
    pit_in_lap=None,
    pit_out_lap=None,
    track_status=None,
    is_accurate=None,
    is_deleted=None,
    deleted_reason=None,
    fastf1_generated=None,
    lap_start_time_ms=None,
    lap_end_time_ms=None,
):
    """Insert or refresh one raw lap and its source quality/context fields."""
    conn.execute(
        text("""
            INSERT INTO lap_times (
                session_id, driver_id, lap_number, lap_time_ms, tire_compound,
                stint_number, tyre_age, fresh_tyre, pit_in_lap, pit_out_lap,
                track_status, is_accurate, is_deleted, deleted_reason,
                fastf1_generated, lap_start_time_ms, lap_end_time_ms
            )
            VALUES (
                :session_id, :driver_id, :lap_number, :lap_time_ms, :tire_compound,
                :stint_number, :tyre_age, :fresh_tyre, :pit_in_lap, :pit_out_lap,
                :track_status, :is_accurate, :is_deleted, :deleted_reason,
                :fastf1_generated, :lap_start_time_ms, :lap_end_time_ms
            )
            ON CONFLICT (session_id, driver_id, lap_number) DO UPDATE SET
                lap_time_ms = EXCLUDED.lap_time_ms,
                tire_compound = EXCLUDED.tire_compound,
                stint_number = EXCLUDED.stint_number,
                tyre_age = EXCLUDED.tyre_age,
                fresh_tyre = EXCLUDED.fresh_tyre,
                pit_in_lap = EXCLUDED.pit_in_lap,
                pit_out_lap = EXCLUDED.pit_out_lap,
                track_status = EXCLUDED.track_status,
                is_accurate = EXCLUDED.is_accurate,
                is_deleted = EXCLUDED.is_deleted,
                deleted_reason = EXCLUDED.deleted_reason,
                fastf1_generated = EXCLUDED.fastf1_generated,
                lap_start_time_ms = EXCLUDED.lap_start_time_ms,
                lap_end_time_ms = EXCLUDED.lap_end_time_ms
        """),
        {
            "session_id": session_id,
            "driver_id": driver_id,
            "lap_number": lap_number,
            "lap_time_ms": lap_time_ms,
            "tire_compound": tire_compound,
            "stint_number": stint_number,
            "tyre_age": tyre_age,
            "fresh_tyre": fresh_tyre,
            "pit_in_lap": pit_in_lap,
            "pit_out_lap": pit_out_lap,
            "track_status": track_status,
            "is_accurate": is_accurate,
            "is_deleted": is_deleted,
            "deleted_reason": deleted_reason,
            "fastf1_generated": fastf1_generated,
            "lap_start_time_ms": lap_start_time_ms,
            "lap_end_time_ms": lap_end_time_ms,
        },
    )


def insert_pit_stop(conn, session_id, driver_id, stop_number, lap, duration_ms):
    conn.execute(
        text("""
            INSERT INTO pit_stops (session_id, driver_id, stop_number, lap, duration_ms)
            VALUES (:session_id, :driver_id, :stop_number, :lap, :duration_ms)
            ON CONFLICT (session_id, driver_id, stop_number) DO UPDATE SET
                lap = EXCLUDED.lap, duration_ms = EXCLUDED.duration_ms
        """),
        {
            "session_id": session_id,
            "driver_id": driver_id,
            "stop_number": stop_number,
            "lap": lap,
            "duration_ms": duration_ms,
        },
    )


def insert_stint(conn, session_id, driver_id, stint_number, compound, lap_start, lap_end):
    conn.execute(
        text("""
            INSERT INTO stints (session_id, driver_id, stint_number, compound, lap_start, lap_end)
            VALUES (:session_id, :driver_id, :stint_number, :compound, :lap_start, :lap_end)
            ON CONFLICT (session_id, driver_id, stint_number) DO UPDATE SET
                compound = EXCLUDED.compound, lap_start = EXCLUDED.lap_start, lap_end = EXCLUDED.lap_end
        """),
        {
            "session_id": session_id,
            "driver_id": driver_id,
            "stint_number": stint_number,
            "compound": compound,
            "lap_start": lap_start,
            "lap_end": lap_end,
        },
    )


def insert_weather(conn, session_id, recorded_at, air_temp, track_temp, humidity, rainfall, wind_speed):
    conn.execute(
        text("""
            INSERT INTO weather (session_id, recorded_at, air_temp, track_temp, humidity, rainfall, wind_speed)
            VALUES (:session_id, :recorded_at, :air_temp, :track_temp, :humidity, :rainfall, :wind_speed)
            ON CONFLICT (session_id, recorded_at) DO UPDATE SET
                air_temp = EXCLUDED.air_temp, track_temp = EXCLUDED.track_temp, humidity = EXCLUDED.humidity,
                rainfall = EXCLUDED.rainfall, wind_speed = EXCLUDED.wind_speed
        """),
        {
            "session_id": session_id,
            "recorded_at": recorded_at,
            "air_temp": air_temp,
            "track_temp": track_temp,
            "humidity": humidity,
            "rainfall": rainfall,
            "wind_speed": wind_speed,
        },
    )


def insert_safety_car_period(
    conn,
    session_id,
    start_time,
    end_time,
    start_lap,
    end_lap,
    period_type,
    end_inferred,
    start_lap_method,
    end_lap_method,
):
    """Insert or refresh one source-timestamped neutralisation period."""
    conn.execute(
        text("""
            INSERT INTO safety_car_periods (
                session_id, start_time, end_time, start_lap, end_lap,
                period_type, end_inferred, start_lap_method, end_lap_method
            )
            VALUES (
                :session_id, :start_time, :end_time, :start_lap, :end_lap,
                :period_type, :end_inferred, :start_lap_method, :end_lap_method
            )
            ON CONFLICT (session_id, start_time, period_type) DO UPDATE SET
                end_time = EXCLUDED.end_time,
                start_lap = EXCLUDED.start_lap,
                end_lap = EXCLUDED.end_lap,
                end_inferred = EXCLUDED.end_inferred,
                start_lap_method = EXCLUDED.start_lap_method,
                end_lap_method = EXCLUDED.end_lap_method
        """),
        {
            "session_id": session_id,
            "start_time": start_time,
            "end_time": end_time,
            "start_lap": start_lap,
            "end_lap": end_lap,
            "period_type": period_type,
            "end_inferred": end_inferred,
            "start_lap_method": start_lap_method,
            "end_lap_method": end_lap_method,
        },
    )


def get_driver_id_by_code(conn, code: str):
    row = conn.execute(text("SELECT driver_id FROM drivers WHERE driver_code = :code"), {"code": code}).first()
    return row[0] if row else None


def safe_int(value):
    try:
        if value is None or (isinstance(value, float) and value != value):
            return None
        return int(value)
    except (ValueError, TypeError):
        return None


def ms_from_timedelta(td):
    """Convert a pandas Timedelta (or NaT/None) to integer milliseconds, or None.

    Uses integer nanosecond math (not total_seconds()*1000) to avoid floating-point
    representation error, which silently truncates the result by 1ms in ~1/116 cases.
    This is the corrected version — see RAG project bug #9.
    """
    if td is None or pd.isna(td):
        return None
    return int(td.value // 1_000_000)
