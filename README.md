# F1 Pit Stop Strategy Optimizer

A data-driven project to recommend a Formula 1 driver's next pit lap and tyre compound. The planned system combines historical F1 data, a tyre-degradation model, a stochastic race simulator, and an optimizer. Recommendations should include the assumptions and uncertainty behind the result.

## Architecture

1. **Data** — FastF1 for the season schedule; OpenF1's free historical API for race laps, driver metadata, and tyre stints.
2. **Storage** — PostgreSQL, accessed with SQLAlchemy and psycopg 3.
3. **Modelling** — an interpretable tyre-degradation baseline followed by a gradient-boosted model when it improves held-out performance.
4. **Simulation** — lap-by-lap remaining-race simulation with sampled safety-car/VSC starts and durations.
5. **Optimization** — compare feasible pit-lap/compound candidates on the same simulated scenarios.
6. **Application** — FastAPI service and React interface after core Python modules are tested.
7. **Evaluation** — hold out entire races/seasons and report prediction error, strategy comparisons, and uncertainty.

Version 1 targets a single driver's next pit decision. Traffic, multi-car tactics, wet-weather transitions, and live race strategy are later extensions.

## Requirements

- Python 3.14 (reference development version; see `.python-version`)
- PostgreSQL accessible from the development machine
- The Python dependencies in `requirements.txt`

FastF1 3.9.x includes Python 3.14 in its published classifiers. The requirements file uses compatible version ranges; for a formal release, record a fully resolved lock file after the environment has been tested.

## Local setup (macOS / Linux)

Run the commands from the repository root:

```bash
python3.14 --version
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Edit `.env` with the connection details for your PostgreSQL instance. Do not commit `.env` or put real credentials in `.env.example`.

Create the database if it does not already exist. For a local PostgreSQL installation, one common option is:

```bash
createdb f1_pitstop_optimizer
```

This command requires your local PostgreSQL role to have permission to create databases. If the database already exists, skip this command.

## Verify the database and create the schema

The database setup modules are currently run from the `backend` directory so `database` is a top-level Python package for these commands:

```bash
cd backend
python -m database.check_connection
python -m database.init_db
cd ..
```

The connection check executes `SELECT 1`; it does not create or modify tables. The initialization command executes the DDL in `backend/database/schema.sql`. The schema statements use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`; schedule metadata columns are added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so the initializer also upgrades an existing database safely.

## Run automated tests

From the repository root, with the virtual environment active:

```bash
python -m pytest -q
```

Tests validate database configuration and ingestion normalization/upsert logic without requiring a live database or live API download.

## Ingest one season's schedule

Start with 2023 as the single-season smoke test. Run both commands from the repository root:

```bash
# Fetch and validate the schedule without writing to PostgreSQL
python -m backend.ingestion.fetch_schedule 2023 --dry-run

# Upsert the season, event metadata, and scheduled sessions
python -m backend.ingestion.fetch_schedule 2023
```

The loader uses FastF1's `get_event_schedule(year, include_testing=False)`. It records each championship event and its named sessions, and stores the schedule's country, location, official event name, and event format on the event row. It prefers `SessionNDateUtc` when storing session dates.

The schedule's `Location` value is a city/region, not a guaranteed official circuit name. The loader therefore leaves `events.circuit_id` unset rather than inventing a circuit identity. Circuit enrichment will be implemented from a reliable source in a later ingestion step.

The loader uses database upserts keyed by the current unique constraints, so repeating the same season load should update records rather than duplicate events or sessions. The dry run validates and prints the fetched schedule without connecting to PostgreSQL.

FastF1's cache is stored under `backend/cache/`; cache files should not be committed.

## Ingest Grand Prix race laps

Run from the repository root after the season schedule has been ingested and the database schema has been initialized.

Start with a dry run of the 2023 Bahrain Grand Prix. It fetches and validates the lap table but does not insert driver or lap rows:

```bash
python -m backend.ingestion.fetch_race_laps 2023 --round 1 --dry-run
```

If the dry run succeeds, ingest that round:

```bash
python -m backend.ingestion.fetch_race_laps 2023 --round 1
```

Then, after checking the first-round data, run all scheduled 2023 Grand Prix Race sessions:

```bash
python -m backend.ingestion.fetch_race_laps 2023
```

The loader requires Race sessions to exist in the local `events` and `sessions` tables. It queries OpenF1 for the season's historical race sessions and matches each stored event by meeting name; it fails if a meeting cannot be matched rather than guessing. The selected race is fetched using OpenF1's `laps`, `stints`, and `drivers` endpoints. A round is committed in its own transaction, and the unique session/driver/lap key makes re-runs update existing lap records.

The loader stores lap time in integer milliseconds, driver name/code, tyre compound, stint number, and derived tyre age when OpenF1 provides enough stint information. OpenF1's `is_pit_out_lap` is retained. FastF1-specific fields that OpenF1 does not expose (for example, deleted-lap/accuracy flags, track status, pit-in marker, fresh-tyre flag, and elapsed lap offsets) remain NULL rather than being invented. Missing lap times remain NULL. The raw-ingestion stage does not filter out in-laps, out-laps, neutralized laps, or inaccurate laps. Rows are skipped only when they have no usable driver code or positive integer lap number, and those exclusions are counted. Explicit cleaning and attrition reporting belong to the next milestone.

[OpenF1's historical API](https://openf1.org/docs/) provides data from 2023 onward without authentication. The free tier is rate-limited, so this loader waits between requests. FastF1 remains used for schedule ingestion only; do not run an F1TV authentication flow for race laps.

## Ingest Grand Prix pit stops

After race laps are present in PostgreSQL, start with a Bahrain 2023 dry run:

```bash
python -m backend.ingestion.fetch_pit_stops 2023 --round 1 --dry-run
```

If it validates the expected pit visits, ingest that round:

```bash
python -m backend.ingestion.fetch_pit_stops 2023 --round 1
```

After inspecting the saved records and repeat-run behavior, process all 2023 Race sessions:

```bash
python -m backend.ingestion.fetch_pit_stops 2023
```

The loader reads OpenF1's `pit` endpoint and the session's `drivers` endpoint, maps each driver number to the existing driver identity, and assigns each driver's pit visits a sequential stop number by timestamp. If an OpenF1 session-level pit query returns HTTP 404, it retries at meeting level and filters records back to the intended Race session by `session_key`; it never imports practice or sprint stops into the race. It preserves missing lap numbers or durations as NULL and reports them. The unique `(session_id, driver_id, stop_number)` key makes repeat runs upsert existing stop records.

**Duration meaning:** `pit_stops.duration_ms` stores the time spent traversing the pit lane (`lane_duration`, or legacy `pit_duration`) in milliseconds. This is not the incremental time lost versus continuing at normal racing pace, and it must not be added directly as strategy pit loss. The strategy model will need a separately calibrated pit-loss estimate.

The OpenF1 historical API is free from 2023 onward but rate-limited; the loader spaces requests. Source details: [OpenF1 API documentation](https://openf1.org/docs/).

## Repository layout

```text
.
├── backend/
│   ├── database/
│   │   ├── connection.py
│   │   ├── init_db.py
│   │   ├── check_connection.py
│   │   └── schema.sql
│   └── ingestion/
│       ├── fetch_schedule.py
│       ├── fetch_race_laps.py
│       ├── fetch_pit_stops.py
│       └── utils.py
├── docs/
│   ├── BUILD_CHECKLIST.md
│   └── PROJECT_PLAN_V5.md
├── tests/
│   ├── test_database_config.py
│   ├── test_schedule_ingestion.py
│   ├── test_race_lap_ingestion.py
│   └── test_pit_stop_ingestion.py
├── .env.example
├── .gitignore
├── .python-version
├── pyproject.toml
├── requirements.txt
└── requirements-dev.txt
```

See [docs/BUILD_CHECKLIST.md](docs/BUILD_CHECKLIST.md) for the milestone definitions, design decisions, and exit conditions.

## Data source note

FastF1 is a community-maintained Python package and is not affiliated with or endorsed by Formula 1. Review the data provider's terms and the repository's dependency licences before distributing derived data.
