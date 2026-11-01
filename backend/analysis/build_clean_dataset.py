"""Build a reproducible baseline tyre-modelling dataset and exclusion audit.

Run from the repository root:
    python -m backend.analysis.build_clean_dataset 2023

Reads stored raw data only. It does not call APIs, mutate PostgreSQL, impute
missing values, or certify that source records are complete. Each excluded lap
gets one primary reason; the first matching rule in EXCLUSION_RULES wins.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import text

from ..database.connection import get_engine

RULESET_VERSION = "1.0.0"
SUPPORTED_COMPOUNDS = frozenset({"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"})

EXCLUSION_RULES = [
    ("deleted_lap", "Source explicitly marks the lap as deleted."),
    ("inaccurate_lap", "Source explicitly marks the lap as inaccurate."),
    ("pit_in_lap", "Source explicitly marks this as a pit-in lap."),
    ("pit_out_lap", "Source explicitly marks this as a pit-out lap."),
    ("non_clear_or_unsupported_track_status", "A non-empty track status other than '1' is recorded."),
    ("missing_or_invalid_lap_time", "Lap time is missing, non-finite, or non-positive."),
    ("missing_or_unsupported_compound", "Compound is missing or outside the supported dry/wet compound labels."),
    ("missing_or_invalid_tyre_age", "Tyre age is missing, non-finite, or negative."),
]

LAP_QUERY = text("""
SELECT sea.year, e.round AS round_number, e.event_name, e.event_date,
       e.circuit_id, c.name AS circuit_name, s.session_id, d.driver_code,
       lt.lap_number, lt.lap_time_ms, lt.tire_compound AS compound,
       lt.tyre_age, lt.stint_number, lt.fresh_tyre, lt.pit_in_lap,
       lt.pit_out_lap, lt.track_status, lt.is_accurate, lt.is_deleted,
       lt.deleted_reason, lt.fastf1_generated, lt.lap_start_time_ms,
       lt.lap_end_time_ms
FROM lap_times lt
JOIN sessions s ON s.session_id = lt.session_id
JOIN events e ON e.event_id = s.event_id
JOIN seasons sea ON sea.season_id = e.season_id
JOIN drivers d ON d.driver_id = lt.driver_id
LEFT JOIN circuits c ON c.circuit_id = e.circuit_id
WHERE sea.year = :year AND s.session_type = 'Race'
ORDER BY e.round, d.driver_code, lt.lap_number
""")

MODEL_FIELDS = [
    "year", "round_number", "event_name", "event_date", "circuit_id",
    "circuit_name", "session_id", "driver_code", "lap_number", "lap_time_ms",
    "lap_time_seconds", "compound", "tyre_age", "stint_number", "fresh_tyre",
    "pit_in_lap", "pit_out_lap", "track_status", "is_accurate", "is_deleted",
    "deleted_reason", "fastf1_generated",
]
EXCLUSION_FIELDS = [
    "year", "round_number", "event_name", "session_id", "driver_code",
    "lap_number", "lap_time_ms", "compound", "tyre_age", "pit_in_lap",
    "pit_out_lap", "track_status", "is_accurate", "is_deleted",
    "exclusion_reason",
]


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "t", "1", "yes"}:
        return True
    if normalized in {"false", "f", "0", "no"}:
        return False
    return None


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _compound(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


def exclusion_reason(row: Mapping[str, Any]) -> str | None:
    """Return the first matching exclusion under the frozen ruleset."""
    if _as_bool(row.get("is_deleted")) is True:
        return "deleted_lap"
    if _as_bool(row.get("is_accurate")) is False:
        return "inaccurate_lap"
    if _as_bool(row.get("pit_in_lap")) is True:
        return "pit_in_lap"
    if _as_bool(row.get("pit_out_lap")) is True:
        return "pit_out_lap"

    track_status = str(row.get("track_status") or "").strip()
    if track_status and track_status != "1":
        return "non_clear_or_unsupported_track_status"

    lap_time_ms = _finite_float(row.get("lap_time_ms"))
    if lap_time_ms is None or lap_time_ms <= 0:
        return "missing_or_invalid_lap_time"

    if _compound(row.get("compound")) not in SUPPORTED_COMPOUNDS:
        return "missing_or_unsupported_compound"

    tyre_age = _finite_float(row.get("tyre_age"))
    if tyre_age is None or tyre_age < 0:
        return "missing_or_invalid_tyre_age"

    return None


def clean_lap_rows(
    raw_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return model-eligible rows and one auditable record per excluded lap."""
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for source in raw_rows:
        row = dict(source)
        reason = exclusion_reason(row)
        if reason:
            excluded.append({
                "year": row.get("year"),
                "round_number": row.get("round_number"),
                "event_name": row.get("event_name"),
                "session_id": row.get("session_id"),
                "driver_code": row.get("driver_code"),
                "lap_number": row.get("lap_number"),
                "lap_time_ms": row.get("lap_time_ms"),
                "compound": _compound(row.get("compound")),
                "tyre_age": row.get("tyre_age"),
                "pit_in_lap": row.get("pit_in_lap"),
                "pit_out_lap": row.get("pit_out_lap"),
                "track_status": row.get("track_status"),
                "is_accurate": row.get("is_accurate"),
                "is_deleted": row.get("is_deleted"),
                "exclusion_reason": reason,
            })
            continue

        row["lap_time_ms"] = int(row["lap_time_ms"])
        row["lap_time_seconds"] = row["lap_time_ms"] / 1000.0
        row["compound"] = _compound(row.get("compound"))
        row["tyre_age"] = float(row["tyre_age"])
        included.append({field: row.get(field) for field in MODEL_FIELDS})
    return included, excluded


def collect_raw_lap_rows(year: int) -> list[dict[str, Any]]:
    """Read stored race laps and their event/driver metadata, without mutation."""
    engine = get_engine()
    try:
        with engine.connect() as connection:
            result = connection.execute(LAP_QUERY, {"year": year}).mappings().all()
        rows = [dict(row) for row in result]
    finally:
        engine.dispose()
    if not rows:
        raise ValueError(f"No stored Race-session lap rows found for {year}.")
    return rows


def _count_by(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(row.get(field)) if row.get(field) is not None else "NULL" for row in rows
    )
    return dict(sorted(counts.items()))


def summarize_cleaning(
    raw_rows: Sequence[Mapping[str, Any]],
    included: Sequence[Mapping[str, Any]],
    excluded: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate attrition and identify critical source fields that are unavailable."""
    reason_counts = _count_by(excluded, "exclusion_reason")
    rounds = sorted({int(row["round_number"]) for row in raw_rows})
    session_ids = {int(row["session_id"]) for row in raw_rows}
    circuit_mapped = {
        int(row["session_id"]) for row in raw_rows if row.get("circuit_name") is not None
    }
    raw_by_round: Counter[int] = Counter(int(row["round_number"]) for row in raw_rows)
    included_by_round: Counter[int] = Counter(int(row["round_number"]) for row in included)
    excluded_by_round: dict[int, Counter[str]] = {}
    event_names = {int(row["round_number"]): row["event_name"] for row in raw_rows}
    for row in excluded:
        excluded_by_round.setdefault(int(row["round_number"]), Counter())[
            str(row["exclusion_reason"])
        ] += 1

    per_round = {}
    for round_no in rounds:
        per_round[round_no] = {
            "round_number": round_no,
            "event_name": event_names[round_no],
            "raw_rows": raw_by_round[round_no],
            "included_rows": included_by_round[round_no],
            "excluded_rows": raw_by_round[round_no] - included_by_round[round_no],
            "exclusions_by_reason": dict(sorted(excluded_by_round.get(round_no, {}).items())),
        }

    return {
        "raw_rows": len(raw_rows),
        "included_rows": len(included),
        "excluded_rows": len(excluded),
        "inclusion_rate": len(included) / len(raw_rows) if raw_rows else 0.0,
        "race_sessions": len(session_ids),
        "race_rounds": len(rounds),
        "exclusion_reason_counts": reason_counts,
        "missing_track_status_rows": sum(row.get("track_status") is None for row in raw_rows),
        "missing_pit_in_flag_rows": sum(row.get("pit_in_lap") is None for row in raw_rows),
        "missing_pit_out_flag_rows": sum(row.get("pit_out_lap") is None for row in raw_rows),
        "missing_lap_start_offset_rows": sum(row.get("lap_start_time_ms") is None for row in raw_rows),
        "missing_lap_end_offset_rows": sum(row.get("lap_end_time_ms") is None for row in raw_rows),
        "sessions_with_circuit_mapping": len(circuit_mapped),
        "sessions_without_circuit_mapping": len(session_ids - circuit_mapped),
        "per_round": per_round,
    }


def render_attrition_report(
    year: int,
    raw_rows: Sequence[Mapping[str, Any]],
    included: Sequence[Mapping[str, Any]],
    excluded: Sequence[Mapping[str, Any]],
    *,
    generated_at: str | None = None,
) -> str:
    summary = summarize_cleaning(raw_rows, included, excluded)
    generated = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# F1 Pit Stop Strategy Optimizer — Cleaning and Attrition Report ({year})",
        "",
        f"- Generated (UTC): {generated}",
        f"- Ruleset version: {RULESET_VERSION}",
        "- Scope: raw stored Grand Prix Race-session laps; one driver-lap row per observation.",
        "- Each excluded row gets one primary reason. The first matching exclusion rule wins.",
        "- This is a preliminary tyre-pace dataset, not the final evaluation dataset.",
        "",
        "## Summary",
        "",
        "| Metric | Rows |",
        "|---|---:|",
        f"| Raw lap rows | {summary['raw_rows']} |",
        f"| Included baseline rows | {summary['included_rows']} |",
        f"| Excluded rows | {summary['excluded_rows']} |",
        f"| Inclusion rate | {summary['inclusion_rate']:.2%} |",
        f"| Race sessions represented | {summary['race_sessions']} |",
        f"| Race rounds represented | {summary['race_rounds']} |",
        "",
        "## Exclusions by primary reason",
        "",
        "| Reason | Rows | Rule |",
        "|---|---:|---|",
    ]
    for reason, description in EXCLUSION_RULES:
        lines.append(f"| {reason} | {summary['exclusion_reason_counts'].get(reason, 0)} | {description} |")
    lines.extend([
        "",
        "## Per-race attrition",
        "",
        "| Round | Race | Raw | Included | Excluded | Reason counts |",
        "|---:|---|---:|---:|---:|---|",
    ])
    for row in summary["per_round"].values():
        breakdown = ", ".join(f"{reason}: {count}" for reason, count in row["exclusions_by_reason"].items()) or "none"
        lines.append(
            f"| {row['round_number']} | {row['event_name']} | {row['raw_rows']} | "
            f"{row['included_rows']} | {row['excluded_rows']} | {breakdown} |"
        )
    lines.extend([
        "",
        "## Source coverage and limitations",
        "",
        f"- Track status is missing for {summary['missing_track_status_rows']} / {summary['raw_rows']} raw rows. Neutralised laps cannot be reliably excluded using the currently persisted data.",
        f"- Pit-in flags are missing for {summary['missing_pit_in_flag_rows']} / {summary['raw_rows']} rows. A missing flag is not interpreted as a normal racing lap.",
        f"- Pit-out flags are missing for {summary['missing_pit_out_flag_rows']} / {summary['raw_rows']} rows; explicitly marked pit-out laps are excluded.",
        f"- Lap start offsets are missing for {summary['missing_lap_start_offset_rows']} / {summary['raw_rows']} rows and end offsets for {summary['missing_lap_end_offset_rows']} / {summary['raw_rows']}. Weather-to-lap joining is therefore not attempted.",
        f"- Formal circuit names are mapped for {summary['sessions_with_circuit_mapping']} / {summary['race_sessions']} race sessions. Event names are not silently substituted for circuit names.",
        "- Weather coverage is partial and is not joined into this dataset.",
        "- No arbitrary high-lap-time cutoff is applied. Unusual values require training-only diagnostics and sensitivity checks.",
        "- A missing stint number alone does not exclude a row when lap time, compound, and tyre age are valid.",
        "- The current source does not support a complete fuel correction, driver/team effects, survival/censoring analysis, tyre-cliff test, or neutralisation filtering. These are still required before final evaluation.",
        "",
        "## Artifacts",
        "",
        f"- data/processed/modeling_laps_{year}.csv — included rows.",
        f"- reports/data_exclusions_{year}.csv — one row per exclusion.",
        f"- reports/data_attrition_{year}.md — this report.",
        f"- reports/cleaning_manifest_{year}.json — rules, counts, and checksums.",
        "",
    ])
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_cleaning_artifacts(
    year: int,
    raw_rows: Sequence[Mapping[str, Any]],
    included: Sequence[Mapping[str, Any]],
    excluded: Sequence[Mapping[str, Any]],
    *,
    data_dir: str | Path = "data/processed",
    reports_dir: str | Path = "reports",
    generated_at: str | None = None,
) -> dict[str, Path]:
    data_path = Path(data_dir)
    report_path = Path(reports_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    report_path.mkdir(parents=True, exist_ok=True)

    modeling_csv = data_path / f"modeling_laps_{year}.csv"
    exclusions_csv = report_path / f"data_exclusions_{year}.csv"
    attrition_md = report_path / f"data_attrition_{year}.md"
    manifest_json = report_path / f"cleaning_manifest_{year}.json"

    with modeling_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=MODEL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(included)
    with exclusions_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=EXCLUSION_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(excluded)
    attrition_md.write_text(
        render_attrition_report(year, raw_rows, included, excluded, generated_at=generated_at),
        encoding="utf-8",
    )

    summary = summarize_cleaning(raw_rows, included, excluded)
    manifest = {
        "year": year,
        "ruleset_version": RULESET_VERSION,
        "generated_at_utc": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_tables": ["seasons", "events", "sessions", "drivers", "lap_times", "circuits"],
        "session_type": "Race",
        "raw_rows": len(raw_rows),
        "included_rows": len(included),
        "excluded_rows": len(excluded),
        "inclusion_rate": summary["inclusion_rate"],
        "supported_compounds": sorted(SUPPORTED_COMPOUNDS),
        "exclusion_priority": [{"reason": reason, "description": description} for reason, description in EXCLUSION_RULES],
        "exclusion_reason_counts": summary["exclusion_reason_counts"],
        "missing_track_status_rows": summary["missing_track_status_rows"],
        "missing_pit_in_flag_rows": summary["missing_pit_in_flag_rows"],
        "missing_pit_out_flag_rows": summary["missing_pit_out_flag_rows"],
        "missing_lap_start_offset_rows": summary["missing_lap_start_offset_rows"],
        "missing_lap_end_offset_rows": summary["missing_lap_end_offset_rows"],
        "sessions_with_circuit_mapping": summary["sessions_with_circuit_mapping"],
        "sessions_without_circuit_mapping": summary["sessions_without_circuit_mapping"],
        "artifacts": {
            "modeling_csv": str(modeling_csv),
            "modeling_csv_sha256": _sha256(modeling_csv),
            "exclusions_csv": str(exclusions_csv),
            "exclusions_csv_sha256": _sha256(exclusions_csv),
            "attrition_report": str(attrition_md),
        },
        "limitations": [
            "Preliminary tyre-pace extract only; not the full evaluation dataset.",
            "Neutralisation filtering is incomplete because track_status is missing.",
            "Pit-in filtering is only possible when a source flag is present.",
            "Lap start/end offsets are missing, so no weather-to-lap join was attempted.",
            "Circuit mapping is incomplete.",
            "Fuel correction, driver/team effects, survival/censoring and tyre-cliff analysis remain unimplemented.",
        ],
    }
    manifest_json.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "modeling_csv": modeling_csv,
        "exclusions_csv": exclusions_csv,
        "attrition_report": attrition_md,
        "manifest_json": manifest_json,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a preliminary cleaned tyre-lap dataset and exclusion audit.")
    parser.add_argument("year", type=int)
    parser.add_argument("--data-dir", default="data/processed")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()

    raw = collect_raw_lap_rows(args.year)
    included, excluded = clean_lap_rows(raw)
    artifacts = write_cleaning_artifacts(
        args.year, raw, included, excluded,
        data_dir=args.data_dir, reports_dir=args.reports_dir,
    )
    summary = summarize_cleaning(raw, included, excluded)

    print(f"Cleaning complete for {args.year}; ruleset {RULESET_VERSION}.")
    print(f"Raw rows: {summary['raw_rows']}; included: {summary['included_rows']}; excluded: {summary['excluded_rows']}.")
    for reason, count in summary["exclusion_reason_counts"].items():
        print(f"  {reason}: {count}")
    print(
        f"Metadata gaps: track status {summary['missing_track_status_rows']}/{summary['raw_rows']}; "
        f"pit-in flags {summary['missing_pit_in_flag_rows']}/{summary['raw_rows']}; "
        f"lap start/end offsets {summary['missing_lap_start_offset_rows']}/"
        f"{summary['missing_lap_end_offset_rows']}."
    )
    for name, path in artifacts.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
