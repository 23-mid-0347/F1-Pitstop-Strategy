# F1 Pit Stop Strategy Optimizer — Project Plan V5

**Handoff date:** 2026-11-01  
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

### Merged into `main`
- Milestone 0 scaffolding, database configuration, and configuration tests.
- `backend/ingestion/fetch_schedule.py`: fetches and validates a season calendar, prints a dry-run summary, and upserts events and named sessions.
- Schedule metadata columns on `events`: country, location, official event name, and event format.
- Documentation for schedule ingestion and initial project setup.

### Verified locally by the user on 2026-11-01
- Python 3.14.5 and the root `.venv` are active.
- `python -m pip install --no-cache-dir -r requirements-dev.txt` completed with dependencies already satisfied.
- The configuration suite passed: `3 passed in 0.23s`.
- PostgreSQL connection check succeeded and schema initialization worked.
- All 11 expected tables were present.
- The 2023 schedule dry-run listed 22 events and 110 sessions.
- A repeated schedule upsert left database counts unchanged: 1 season, 22 events, 110 sessions.

### Current feature branch, not yet locally verified or merged
Branch: `feat/race-lap-ingestion`

- Updates FastF1 cache setup from deprecated `Cache.enable_cache` to `Cache.configure`.
- Adds `backend/ingestion/fetch_race_laps.py` with one-round dry-run support and an all-race-seasons mode.
- Adds lap metadata columns for stint number, tyre age/life, fresh-tyre flag, pit-in/out flags, track status, accuracy/deletion flags, FastF1-generated-row flag, and lap start/end session offsets.
- Extends lap upserts to update all stored source fields for an existing session/driver/lap key.
- Adds `tests/test_race_lap_ingestion.py` for normalization, missing values, source flags, duplicate identities, dry-run behavior, upsert inputs, and modern cache setup.
- Updates README and checklist for race-lap ingestion and current FastF1 authentication behaviour.

### Core file layout

```text
backend/
  database/
    connection.py
    init_db.py
    check_connection.py
    schema.sql
  ingestion/
    utils.py
    fetch_schedule.py
    fetch_race_laps.py       # feature branch only until merged
tests/
  test_database_config.py
  test_schedule_ingestion.py
  test_race_lap_ingestion.py # feature branch only until merged
docs/
  BUILD_CHECKLIST.md
  PROJECT_PLAN_V5.md
```

### Code and data-handling rules to preserve
- SQLAlchemy uses the `postgresql+psycopg` dialect (psycopg 3).
- `init_db.py` executes the SQL statements in `schema.sql`; keep semicolons out of SQL comments because the current initializer splits on semicolons.
- Use integer nanosecond arithmetic for timedelta conversion and preserve explicit millisecond units in PostgreSQL.
- Driver-name enrichment remains deferred. When the source provides only the three-letter code, use that as `full_name`, not NULL.
- Cache files under `backend/cache/` must remain ignored by Git.
- A remote commit or passing mocked tests do not prove that a live FastF1 download or PostgreSQL lap upsert was run locally.

## 3. Local environment and database handoff

As of 2026-11-01, the user reported the following successful local checks on macOS:
- Python version: `3.14.5`; project virtual environment: `.venv`.
- PostgreSQL: `localhost:5432`, database `f1_pitstop_optimizer`, role `shyamprasad`.
- `python -m database.check_connection` succeeded.
- Database schema is initialized, with 11 base tables plus schedule metadata columns on `events`.
- The 2023 schedule is stored and has been verified as repeatable: 1 season, 22 events, and 110 sessions after a second load.
- The schedule loader still showed the FastF1 `Cache.enable_cache` deprecation warning on the user's merged `main`; the feature branch replaces it with `Cache.configure`.

### Local environment variables

Expected root `.env` values (local only; never commit secrets):

    DB_HOST=localhost
    DB_PORT=5432
    DB_NAME=f1_pitstop_optimizer
    DB_USER=shyamprasad
    DB_PASSWORD=

Set `DB_PASSWORD` only as required by the local PostgreSQL role. Do not paste credentials into chat or add them to GitHub.

### FastF1 authentication note

FastF1 3.9.0 documents that most historical timing data may require an active F1TV Access/Pro/Premium subscription. If lap loading prompts for authentication, check status and follow the interactive login process:

    python -m fastf1 auth f1tv --status
    python -m fastf1 auth f1tv --authenticate

Do not store credentials in repository files. FastF1 stores a local token and may require re-authentication when it expires. Official docs: https://github.com/theOehrly/Fast-F1/blob/main/docs/api_reference/accounts_auth.rst

## 4. Current database design

| Table | Purpose |
|---|---|
| drivers | Driver code, required full name, nationality, date of birth |
| constructors | Team/constructor name and nationality |
| circuits | Circuit name, location, country |
| seasons | Unique season year |
| events | Season, round, event name, optional circuit link, date, country, location, official event name, event format |
| sessions | Event, session type, session date |
| lap_times | Session/driver/lap, time in milliseconds, compound, stint number, source tyre age/freshness, pit flags, track status, accuracy/deletion flags, generated-row flag, lap start/end session offsets (new lap-ingestion columns are on `feat/race-lap-ingestion` pending local verification) |
| pit_stops | Session/driver/stop number, lap, duration in milliseconds |
| stints | Session/driver/stint number, compound, start/end lap |
| weather | Timestamp, air/track temperature, humidity, rainfall, wind |
| safety_car_periods | Session, start/end lap, SC/VSC/RedFlag type |

The feature branch adds nullable lap-level `tyre_age`, `stint_number`, `fresh_tyre`, pit-in/out flags, track status, accuracy/deletion flags, a FastF1-generated-row flag, and session-relative lap start/end offsets. The loader preserves supplied source values; it does not infer tyre age when FastF1 omits it. These columns must be applied with `python -m database.init_db` from `backend/` before writing lap data.

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
- [x] Finish/verify dependency installation (reported satisfied in the local environment).
- [x] Run python -m pytest -q (3 passed in 0.23s).
- [x] Verify f1_pitstop_optimizer exists and the configured role can connect.
- [x] Run python -m database.check_connection successfully.
- [x] Run python -m database.init_db successfully.
- [x] Verify all eleven tables using psql and \\dt.

**Exit:** install, tests, DB connection, schema initialization and table listing all pass locally.

### Milestone 1 — one-season ingestion
- [x] Add schedule/event/session loader and mocked tests; merge to `main`.
- [x] Verify 2023 schedule locally: 22 events, 110 sessions, with idempotent repeat loads.
- [x] Add OpenF1 historical race-lap ingestion and mocked tests on `feat/race-lap-ingestion`.
- [x] Apply lap metadata schema changes with `python -m database.init_db`.
- [x] Dry-run and ingest Bahrain 2023, then repeat it: 1,058 lap rows and 1,058 unique identities remained after re-run.
- [x] Ingest all 22 2023 Race sessions: 24,479 lap rows processed.
- [x] Query all race rounds in PostgreSQL: `lap_rows` matched `unique_laps` in each round. Source nulls observed: 118 missing lap times and 92 each missing compound, tyre age and stint number.
- [x] Handle OpenF1 session metadata correctly by joining `meetings` and `sessions` on `meeting_key`, and select `session_name == "Race"` to exclude Sprint sessions.
- [x] Add pit-stop ingestion with dry-run support and mocked tests, now using Jolpica's Ergast-compatible historical `pitstops` and `results` endpoints. OpenF1's `/pit` returned HTTP 404 for both Bahrain session and meeting queries.
- [ ] Pull and locally test the Jolpica pit-stop loader; dry-run Bahrain, inspect pit counts/driver mappings/nulls, ingest round 1, and verify idempotency.
- [ ] Run pit-stop ingestion across 2023 after the one-round checks pass.
- [ ] Add weather ingestion.
- [ ] Add track-status-to-lap mapping for SC/VSC/red-flag periods with a documented mid-lap policy.
- [ ] Populate/validate the separate `stints` table if it adds reliable value beyond lap-level compound, stint number and tyre age.
- [ ] Recheck foreign keys and idempotency before closing Milestone 1.

**Exit:** schedule, lap and pit-stop ingestion is repeatable, and source values, nulls, units and failures are documented.

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

The user has locally verified the 2023 schedule and the full race-lap ingestion on `feat/race-lap-ingestion`. Results:
- 22 events and 110 sessions in the schedule.
- 24,479 lap rows processed across the 22 2023 Grand Prix Race sessions.
- All 22 rounds had equal `lap_rows` and `unique_laps` in the PostgreSQL quality query.
- Bahrain was re-ingested and remained at 1,058 rows and 1,058 unique lap identities.
- Observed source nulls: 118 lap times and 92 each for compound, tyre age and stint number.

The first pit-stop loader used OpenF1's `/pit` endpoint, but live requests returned HTTP 404 for both Bahrain session key 7953 and meeting key 1141. To avoid treating missing OpenF1 results as pit stops or making up values, the loader is now switched to Jolpica's Ergast-compatible historical `pitstops` and `results` endpoints. This latest implementation and its tests are not locally verified yet.

From the repository root:
```bash
git pull --ff-only origin feat/race-lap-ingestion
python -m pytest -q
cd backend
python -m database.init_db
cd ..
python -m backend.ingestion.fetch_pit_stops 2023 --round 1 --dry-run
```

Verify that the test suite passes, the schema initialization succeeds, and the dry-run reports a plausible Bahrain stop list with driver codes and source durations. Only then write Bahrain pit-stop rows:
```bash
python -m backend.ingestion.fetch_pit_stops 2023 --round 1
```

Query pit-stop row counts, check driver foreign keys and nulls, then re-run round 1 and verify the count does not increase. Do not run full-season pit-stop ingestion or merge the PR before those checks pass.

Pit duration is the source-reported duration converted to milliseconds. It is not incremental time lost versus normal green-flag racing pace, and may include pit entry/exit or red-flag delay in unusual cases. Do not add the raw duration directly into simulated time. Keep credentials private and do not attempt F1TV authentication for this project path.

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
