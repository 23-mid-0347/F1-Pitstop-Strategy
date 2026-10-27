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

The application modules are run from the `backend` directory so `database` is a top-level Python package for these commands:

```bash
cd backend
python -m database.check_connection
python -m database.init_db
cd ..
```

The connection check executes `SELECT 1`; it does not create or modify tables. The initialization command executes the DDL in `backend/database/schema.sql`. The schema statements are designed to be safe to run again with `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`.

## Run automated configuration tests

From the repository root, with the virtual environment active:

```bash
python -m pytest -q
```

These smoke tests validate required environment settings and safe PostgreSQL URL construction without requiring a live database. The connection command above is the separate live-database check.

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
│       └── utils.py
├── docs/
│   └── BUILD_CHECKLIST.md
├── tests/
│   └── test_database_config.py
├── .env.example
├── .gitignore
├── .python-version
├── pyproject.toml
├── requirements.txt
└── requirements-dev.txt
```

## Milestones

See [docs/BUILD_CHECKLIST.md](docs/BUILD_CHECKLIST.md) for the agreed design decisions, milestones, exit conditions, and validation plan.

## Data source note

FastF1 is a community-maintained Python package and is not affiliated with or endorsed by Formula 1. Review the data provider's terms and the repository's dependency licences before distributing derived data.
