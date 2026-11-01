# F1 Pit Stop Strategy Optimizer

A data-driven project to recommend a Formula 1 driver's next pit lap and tyre compound. The planned system combines historical F1 data, a tyre-degradation model, a stochastic race simulator, and an optimizer. Recommendations should include the assumptions and uncertainty behind the result.

## Architecture

1. **Data** — FastF1 timing, lap, tyre/stint, pit-stop, weather, and track-status data.
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

Tests validate database configuration and the schedule-ingestion normalization/upsert flow without requiring a live database or live FastF1 download.

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

The loader requires the Race sessions to exist in the local `events` and `sessions` tables. It retrieves each race with `fastf1.get_session(year, round, "R")` and loads laps and race-control messages without car telemetry or weather. A round is committed in its own transaction, and the unique session/driver/lap key makes re-runs update existing lap records.

The loader preserves lap time in integer milliseconds, the source tyre compound, source stint number, `TyreLife`, `FreshTyre`, pit-in/out indicators, track status, deleted/accuracy flags, FastF1-generated-row indicator, and lap start/end offsets in milliseconds. Missing lap times remain NULL. The initial raw-ingestion stage does not filter out in-laps, out-laps, SC/VSC laps, deleted laps, or inaccurate laps. Rows are skipped only when they have no usable driver code or positive integer lap number, and those exclusions are counted. Explicit cleaning and attrition reporting belong to the next milestone.

FastF1 3.9 supports F1TV authentication, and most historical timing data may require an active F1TV Access/Pro/Premium account. Check authentication status with:

```bash
python -m fastf1 auth f1tv --status
```

If authentication is needed, follow FastF1's interactive process:

```bash
python -m fastf1 auth f1tv --authenticate
```

Do not paste credentials into chat or commit them to Git. FastF1 stores authentication tokens locally. See the [FastF1 authentication documentation](https://github.com/theOehrly/Fast-F1/blob/main/docs/api_reference/accounts_auth.rst).

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
│       └── utils.py
├── docs/
│   ├── BUILD_CHECKLIST.md
│   └── PROJECT_PLAN_V5.md
├── tests/
│   ├── test_database_config.py
│   └── test_schedule_ingestion.py
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
