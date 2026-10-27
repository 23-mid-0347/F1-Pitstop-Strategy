# F1 Pit Stop Strategy Optimizer — Project Plan V5

**Handoff date:** 2026-10-27  
**Repository:** https://github.com/23-mid-0347/F1-Pitstop-Strategy  
**Default branch:** main  
**Purpose:** Current source-of-truth handoff for continuing work in a new chat. It consolidates the original v4 plan, technical decisions, repository updates, local Mac setup, database state, and the end-to-end checklist.

> Verify the live repository before editing. A file or remote commit is not proof that it has run locally.

## 1. Project objective

Given a driver's current race state, recommend the next pit lap and tyre compound. Version 1 is a single-driver strategy-support prototype that minimizes expected remaining race time under explicit assumptions. It is not a team-grade simulator and does not predict finishing position.

Pipeline:
1. Ingest historical F1 data from FastF1.
2. Store it in PostgreSQL.
3. Build a clean, reproducible modelling dataset.
4. Fit an interpretable tyre-degradation baseline and compare a gradient-boosted model.
5. Simulate remaining laps with pit loss and stochastic SC/VSC events.
6. Compare candidate pit laps/compounds on common simulated scenarios.
7. Expose core functions through FastAPI and display recommendations in React.
8. Backtest on races withheld from training and report performance and uncertainty.

Keep ingestion, cleaning, model, simulator, optimizer, API and UI as separate testable modules.

## 2. Repository state

Repository: https://github.com/23-mid-0347/F1-Pitstop-Strategy

Milestone 0 changes committed to main:
- Fixed .gitignore to exclude the actual FastF1 cache path backend/cache/ as well as other local/build artifacts.
- Kept .env out of Git and updated the placeholder-only .env.example.
- Added .python-version with Python 3.14 as reference, requirements.txt, requirements-dev.txt, pyproject.toml, package markers and README.md.
- Standardized schema initialization as python -m database.init_db from the backend directory.
- Added backend/database/check_connection.py.
- Added tests/test_database_config.py.
- Added docs/BUILD_CHECKLIST.md and this handoff document.

Core files currently present:
- backend/database/schema.sql
- backend/database/connection.py
- backend/database/init_db.py
- backend/database/check_connection.py
- backend/ingestion/utils.py
- README.md, requirements.txt, requirements-dev.txt, pyproject.toml
- tests/test_database_config.py
- docs/BUILD_CHECKLIST.md

Code-level facts observed from the repository:
- SQLAlchemy uses the postgresql+psycopg dialect (psycopg 3).
- init_db executes statements from schema.sql.
- ingestion/utils.py contains FastF1 cache setup, upsert helpers and ms_from_timedelta() using integer nanosecond arithmetic (td.value // 1_000_000).
- upsert_driver updates both full_name and nationality.
- FastF1 cache path is backend/cache; keep it ignored by Git.

## 3. Local environment and database handoff

From the current conversation:
- macOS; Python 3.14.5 confirmed.
- Root .venv was created and pip was upgraded to 26.2.2.
- Installing requirements started but was cancelled with Ctrl+C. Installation is not confirmed complete. Pip showed cache deserialization warnings; retry without using cache.
- PostgreSQL responds on localhost:5432. The command psql -h localhost -p 5432 -d postgres -c "\\l" succeeded.
- Existing f1_analytics database is owned by role shyamprasad. Keep it separate and do not use it for this project.
- The first database listing did not show f1_pitstop_optimizer. The user later reported creating that database, but no post-creation database list or successful connection output was supplied.
- A prior database connection check failed before the user reported creating the database. Re-run it now.
- Schema initialization has not been confirmed.

### Local .env settings

Expected values (local only; do not commit credentials):

    DB_HOST=localhost
    DB_PORT=5432
    DB_NAME=f1_pitstop_optimizer
    DB_USER=shyamprasad
    DB_PASSWORD=

Fill DB_PASSWORD with the actual password if local PostgreSQL authentication requires it. Never place real credentials in this document or in GitHub.

### Exact next commands

From repository root:

    source .venv/bin/activate
    python -m pip install --no-cache-dir -r requirements-dev.txt
    python -m pytest -q

Verify the database exists and can be accessed:

    psql -h localhost -p 5432 -U shyamprasad -d postgres -c "\\l"
    psql -h localhost -p 5432 -U shyamprasad -d f1_pitstop_optimizer -c "SELECT current_user, current_database();"

Then initialize the schema:

    cd backend
    python -m database.check_connection
    python -m database.init_db
    cd ..
    psql -h localhost -p 5432 -U shyamprasad -d f1_pitstop_optimizer -c "\\dt"

Expected tables: drivers, constructors, circuits, seasons, events, sessions, lap_times, pit_stops, stints, weather, safety_car_periods.

Do not start ingestion until package installation/tests pass, DB connection works, schema initialization succeeds, and all eleven tables appear. For errors, keep the full text but redact credentials. Diagnose server status, DB name, role and authentication separately.

## 4. Current database design

| Table | Purpose |
|---|---|
| drivers | Driver code, required full name, nationality, date of birth |
| constructors | Team/constructor name and nationality |
| circuits | Circuit name, location, country |
| seasons | Unique season year |
| events | Season, round, event name, circuit, date |
| sessions | Event, session type, session date |
| lap_times | Session/driver/lap, lap time in milliseconds, tyre compound |
| pit_stops | Session/driver/stop number, lap, duration in milliseconds |
| stints | Session/driver/stint number, compound, start/end lap |
| weather | Timestamp, air/track temperature, humidity, rainfall, wind |
| safety_car_periods | Session, start/end lap, SC/VSC/RedFlag type |

The schema does not currently store explicit tyre age for every lap. Before modelling, preserve FastF1 tyre-life/age where available and define a validated field in the lap table or a reproducible modelling dataset. Document provenance and missing-value handling.

Other database rules:
- Keep this database separate from f1_analytics and the F1 RAG project.
- The results table was deliberately excluded from the initial schema. Revisit only if a concrete evaluation needs official results or finishing position.
- Driver name enrichment was deferred. If only a three-letter code is available, use full_name=driver_code; do not insert NULL into the required column.
- Check every upsert conflict clause for all fields expected to be refreshed and test idempotency.
- Keep exact integer nanosecond duration conversion; avoid float seconds multiplied by 1000.
- CREATE TABLE IF NOT EXISTS does not migrate existing columns. Use deliberate migrations once data exists.
- pit_stops.duration_ms represents pit-lane entry-to-exit duration in this plan, not incremental time lost against racing. Do not add the raw duration directly to simulated time. Calibrate incremental pit loss against a green-flag reference, then model SC/VSC discounts separately.

## 5. Locked modelling decisions

### Data and validation
1. Start with one complete season. Expand only after ingestion and data checks pass.
2. Ingest other sessions if useful, but train the baseline on Grand Prix Race sessions only. Sprint inclusion is a separate experiment.
3. Preserve ingested data and create a clean modelling dataset with explicit exclusion reasons and attrition counts.
4. Define and test rules for pit/in/out laps, yellow/SC/VSC laps, red-flag affected laps, invalid times, missing tyre age and other non-representative observations. Do not silently drop rows.
5. Split by race or season/time. Do not randomly place laps from the same race in both train and test.
6. Lap time is influenced by traffic, driver pace, track evolution, fuel, weather and flags as well as tyre wear. State this limitation.

### Tyre-degradation model
- Baseline: interpretable per-compound quadratic or piecewise regression against tyre age.
- Use circuit-specific effects only where sample coverage supports them; otherwise pool circuits or use circuit types.
- Advanced model: gradient-boosted regression with tyre age, compound, circuit, track temperature and a clearly labelled lap-number-derived fuel-load proxy if useful.
- Compare held-out-race RMSE and MAE. Inspect residuals, implausible predictions and high-tyre-age extrapolation.
- Use the advanced model only if data supports the complexity. Save model metadata, features, training races and metrics.

### Race simulator and stochastic events
- Simulate remaining laps from the degradation model, compound changes and calibrated incremental pit loss.
- Sample SC/VSC event start lap and duration; do not represent contiguous neutralisations only as independent one-lap surprises.
- SC and VSC need distinct pit-loss assumptions.
- Treat red flags separately. Specify interruption/restart/tyre-change assumptions or mark those cases unsupported.
- Use fixed seeds for repeatable tests.
- Generate one common set of seeded scenarios per optimization request and score all candidates on those same scenarios. Common random numbers reduce Monte Carlo noise in differences.
- For M runs report mean, standard deviation, standard error (s / sqrt(M)) and a confidence interval. For strategy differences, use paired per-scenario differences and report uncertainty.

### Optimizer
- V1 objective: minimize expected remaining race time for one driver.
- Search feasible candidate next pit laps and compounds.
- Grid search first. Consider dynamic programming only after timing grid search and defining state transitions; compare both on small toy cases.
- Return recommended lap/compound, expected remaining time, uncertainty, runner-up candidates and a readable “pit now vs. pit later” explanation.
- Enforce relevant rules for supported scenarios. Never rank an infeasible strategy as optimal.
- State the search horizon. Do not claim global optimality if only one or a bounded number of future stops are searched.
- Finishing position, opponent pace, traffic, undercut/overcut interactions, multi-car tactics, wet-weather transitions and live strategy are later scope.

### API, UI and evaluation
- Test core Python functions without API/UI dependencies.
- Planned FastAPI endpoints: /health, /predict-laptime, /simulate-strategy, /optimize-pitstop.
- Use validated schemas, explicit units, typed results and parameterized SQL. For nullable PostgreSQL filters, use explicit casts where needed, e.g. CAST(:param AS INTEGER) IS NULL, to avoid psycopg 3 ambiguous-parameter errors.
- Build React after core modules/API are tested. Show assumptions, alternatives and uncertainty.
- Backtest on withheld races. Compare historical strategy and simple baselines. Report prediction error separately from decision outcomes; explain disagreements without assuming teams were wrong.

## 6. Dependencies

Current ranges in requirements.txt:
- FastF1 >=3.9.0,<4.0.0
- pandas >=2.1.1,<3.0.0
- psycopg[binary] >=3.2.0,<4.0.0
- python-dotenv >=1.0.0,<2.0.0
- SQLAlchemy >=2.0.44,<3.0.0
- Development: pytest >=8.3.0,<10.0.0

These are compatible version ranges, not a fully pinned lock file. After clean installation and tests, create a resolved lock file if release-level reproducibility requires exact versions.

## 7. Master checklist

Checkboxes distinguish code already committed from local checks still pending. Completion means the exit criteria are tested, not merely that files exist.

### Milestone 0 — repository and environment
- [x] Fix cache ignore path.
- [x] Keep secrets out of Git and provide placeholder .env example.
- [x] Add requirements files and Python reference version.
- [x] Add package markers and standard run commands.
- [x] Add README, pytest configuration and configuration tests.
- [x] Add live DB connection check and repository checklist.
- [ ] Finish dependency installation (previous attempt was cancelled).
- [ ] Run python -m pytest -q.
- [ ] Verify f1_pitstop_optimizer exists and .env settings work.
- [ ] Run python -m database.check_connection successfully.
- [ ] Run python -m database.init_db successfully.
- [ ] Verify all eleven tables using psql and \\dt.

**Exit:** install, tests, DB connection, schema initialization and table listing all pass locally.

### Milestone 1 — one-season ingestion
- [ ] Implement schedule/event/session ingestion first.
- [ ] Add lap ingestion with units, missing values and source IDs.
- [ ] Add stint/compound and tyre-age provenance.
- [ ] Add pit stops, weather and track-status periods (SC/VSC/red flag).
- [ ] Document track-status timestamp-to-lap mapping and mid-lap policy.
- [ ] Test upsert completeness and idempotency.
- [ ] Load one complete season; validate counts, foreign keys, nulls, units and failed-session logs.

**Exit:** one-season ingestion is repeatable and does not duplicate rows.

### Milestone 2 — data quality
- [ ] Build clean Race-session dataset and explicit filter rules.
- [ ] Produce attrition report by exclusion reason and race.
- [ ] Validate compound labels, tyre-age sequences, pit stops and neutralisation periods.
- [ ] Explore data support by compound/circuit/season; plot lap time vs. tyre age.
- [ ] Finalize race-wise/temporal holdout split.

**Exit:** reproducible modelling dataset with documented coverage and limitations.

### Milestone 3 — degradation model
- [ ] Fit regression baseline and gradient-boosted model.
- [ ] Compare held-out RMSE/MAE and inspect residuals/extrapolation.
- [ ] Save the chosen artifact and training/data metadata.

**Exit:** fair, reproducible model comparison.

### Milestone 4 — simulator
- [ ] Define typed race-state input.
- [ ] Predict remaining lap times and include incremental pit loss.
- [ ] Add SC/VSC start/duration sampling and separate red-flag policy.
- [ ] Share common random scenarios across candidates.
- [ ] Test boundary cases and seeded reproducibility.

**Exit:** hand-checkable toy cases and reproducible Monte Carlo runs.

### Milestone 5 — optimizer
- [ ] Enumerate feasible pit laps and compounds.
- [ ] Enforce constraints for supported scenarios.
- [ ] Score candidates on the same scenarios.
- [ ] Report best candidate, uncertainty, alternatives and explanation.
- [ ] Test toy optima and benchmark runtime.
- [ ] Evaluate DP only if grid search performance justifies it.

**Exit:** feasible, reproducible recommendation with uncertainty.

### Milestone 6 — API and frontend
- [ ] Implement /health, /predict-laptime, /simulate-strategy, /optimize-pitstop.
- [ ] Add API schemas, error handling and tests.
- [ ] Build React form/result view with assumptions, alternatives and uncertainty.

**Exit:** user submits a supported race state and gets an explained recommendation.

### Milestone 7 — backtesting
- [ ] Freeze held-out races/seasons.
- [ ] Compare with actual strategy and simple baselines.
- [ ] Report model prediction metrics separately from strategy metrics.
- [ ] Use paired scenario differences and confidence intervals.
- [ ] Run ablations, inspect failures and document limitations.

**Exit:** claims are supported by reproducible experiments; inconclusive results are reported honestly.

### Milestone 8 — delivery
- [ ] Add lint/test CI.
- [ ] Add Docker after local setup is stable.
- [ ] Add reproducible demo, architecture/data dictionary/model assumptions.
- [ ] Add lock file as needed.
- [ ] Update conference paper using measured results only.

**Exit:** another developer can set up, test and reproduce the project from README.

## 8. Exact immediate next step

1. Complete install: python -m pip install --no-cache-dir -r requirements-dev.txt
2. Run tests: python -m pytest -q
3. Verify project DB access with the psql command in Section 3.
4. From backend/, run python -m database.check_connection and python -m database.init_db.
5. Confirm the eleven tables with psql and \\dt.
6. Share errors with secrets redacted; troubleshoot based on output.
7. Only after Milestone 0 passes, begin Milestone 1 with schedule/event/session ingestion.

Do not start modelling before ingestion and data quality are reliable.

## 9. Research positioning

Pit-stop optimization already exists in academic literature. Position this project as a transparent open-data pipeline joining a calibrated tyre model, event-duration-aware stochastic simulator, optimizer and reproducible held-out evaluation—not as inventing pit-stop optimization.

- FastF1: https://github.com/theOehrly/Fast-F1
- FastF1 data reference: https://github.com/theOehrly/Fast-F1/blob/main/docs/data_reference/index.rst
- Aguad & Thraves (2024), dynamic programming/game theory: https://doi.org/10.1016/j.ejor.2024.07.011
- Thomas et al. (2026), reinforcement learning for F1 strategy: https://doi.org/10.1007/s10994-026-07081-3
- scikit-learn GroupKFold: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html

These references inform design; they do not prove this project's implementation is validated. Review third-party licences before using external code.

## 10. Starting prompt for a new chat

Attach this file and paste:

I am building the F1 Pit Stop Strategy Optimizer at https://github.com/23-mid-0347/F1-Pitstop-Strategy. Read Project Plan V5 completely and treat it as the handoff source. We are at Milestone 0: scaffolding is committed and pulled; Python 3.14.5 and .venv exist; dependency installation was interrupted. PostgreSQL responds on localhost:5432. The database f1_pitstop_optimizer was reported created, with local role likely shyamprasad, but post-creation connection/schema initialization are not confirmed. First help me run the exact verification commands, troubleshoot errors based on evidence, and only then start Milestone 1 schedule/event/session ingestion. Inspect the live repo before edits, don't jump ahead, and don't invent results.
