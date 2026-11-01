# F1 Pit Stop Strategy Optimizer — Plan Alignment and Build Checklist

Repository: https://github.com/23-mid-0347/F1-Pitstop-Strategy  
Plan reviewed: Project Plan V5  
Review date: 2026-11-01

## Goal

Given a driver's current race state, recommend a pit lap and next tyre compound by combining (1) a tyre-degradation model, (2) a lap-by-lap stochastic race simulator, and (3) a strategy optimizer. The recommendation should include the expected outcome, uncertainty, and understandable trade-offs.

## Repository snapshot reviewed

The default branch is `main`. The visible tree currently includes:
- `.env.example`
- `.gitignore`
- `backend/database/connection.py`
- `backend/database/init_db.py`
- `backend/database/schema.sql`
- `backend/ingestion/utils.py`

Reviewed source files directly through GitHub:
- Schema defines drivers, constructors, circuits, seasons, events, sessions, lap_times, pit_stops, stints, weather, and safety_car_periods.
- SQLAlchemy connection uses the `postgresql+psycopg://` dialect.
- Database initialization executes `schema.sql`.
- Shared ingestion utilities include upserts and a `ms_from_timedelta` implementation based on integer nanosecond arithmetic.
- `.gitignore` ignores `fastf1_cache/`, while `utils.py` configures the cache at `backend/cache`. Fix before ingestion.
- The usage comment in `backend/database/init_db.py` should align with package execution from the backend root (recommended: `python -m database.init_db`).

This is a repository-source review, not a claim that the local database or end-to-end pipeline has been executed.

## Architecture recommendation

FastF1 schedule + OpenF1 historical race data -> ingestion and quality checks -> PostgreSQL -> modelling dataset -> tyre model -> stochastic race simulator -> strategy optimizer -> FastAPI -> React interface -> backtest/evaluation.

Keep the degradation model, simulator, and optimizer as separate modules with explicit inputs and outputs. This helps unit testing and makes recommendations explainable.

## Decisions to adopt

1. **Initial data coverage:** ingest one complete season for a smoke test. Expand to additional seasons only after the loader and quality checks are reliable.
2. **Training sessions:** ingest session types when useful, but train the first model on Grand Prix Race sessions only. Keep Sprint data out of the baseline training set and evaluate inclusion later as a separate experiment.
3. **Lap filtering:** preserve raw rows, create a reproducible clean modelling dataset, and log each exclusion reason. Exclude pit/in-laps, out-laps, yellow/SC/VSC-affected laps, red-flag interruptions/restarts where identifiable, invalid times, and non-representative laps according to explicit documented rules. Do not silently delete data.
4. **Validation split:** do not randomly split laps from the same race across train and test. Use race/group-based holdout; ideally reserve later season(s) for temporal evaluation.
5. **Baseline:** interpretable per-compound quadratic or piecewise regression against tyre age, with circuit/circuit-type effects only where data supports them.
6. **Advanced model:** gradient-boosted regression using tyre age, compound, circuit, track temperature, and an explicitly labelled fuel-load proxy. Compare against baseline with held-out-race RMSE and MAE.
7. **Safety-car sampling:** sample a neutralisation start lap and duration from an empirically estimated distribution or a suitably regularized event model. Use per-circuit estimates only if enough observations exist; otherwise back off to a wider prior. SC and VSC should have distinct pit-loss assumptions.
8. **Common random numbers:** generate reproducible scenarios once for each optimization request and score every candidate strategy against the same scenarios, using fixed seeds in tests.
9. **Red flags:** don't apply the SC/VSC pit-loss discount blindly. Model the red-flag interruption as a separate event with explicit assumptions for stoppage/restart and available tyre change opportunities. Report the simplification and make it configurable.
10. **Monte Carlo reporting:** for M simulation runs report mean, standard deviation, standard error (s/sqrt(M)), and a confidence interval. For strategy deltas, use paired differences across shared scenarios and report their uncertainty.
11. **Objective:** the initial version minimizes expected remaining race time for a single driver. A later version may optimize a risk-sensitive objective or expected finishing position once opponent pace, traffic and track position are modelled.
12. **Optimizer:** implement and benchmark grid search first. Consider dynamic programming only after defining a valid state, transition, tyre/stop constraints, and stochastic event treatment; compare both solutions on small cases.
13. **API:** build/test FastAPI endpoints after core Python functions work in ordinary unit tests. Use request/response schemas, validation, typed outputs, and parameterized SQL. Avoid optional SQL parameters with ambiguous PostgreSQL types; cast optional parameters.
14. **Reproducibility:** record data coverage, source season/session identifiers, cleaning rules, model version, simulation count, random seed, assumptions, and evaluation metrics with each run.
15. **Scope limits:** version 1 is a single-driver strategy support model, not an official team simulator. Traffic, overtaking, multi-car undercut/overcut games, live ingestion, and full wet-weather transitions are later scope unless required.
16. **Tyre age must be explicit:** add a per-lap tyre-age field (and preferably source stint number/fresh-tyre indicator) to `lap_times` or the clean modelling dataset. Stint start/end laps alone cannot reliably identify the age of a used tyre fitted during a race. Use source tyre age when available; with OpenF1, derive lap-level age from the stint's tyre_age_at_start and lap offset, and document missing-value handling.
17. **Pit loss must be calibrated correctly:** `pit_stops.duration_ms` records pit-lane entry-to-exit duration, not the extra time lost versus racing at normal pace. Do not add that raw duration directly to lap-time sums. Estimate circuit/session pit loss as incremental time relative to a green-flag reference, and model the SC/VSC reduction as a separate calibrated assumption.
18. **Legal strategy constraints:** version 1 should expose mandatory compound/stop rules as explicit configurable constraints, using the relevant season's regulations. Never rank an infeasible strategy as optimal. Wet and red-flag cases should be flagged unsupported until their rules are implemented and tested.
19. **V1 recommendation scope:** recommend the next pit decision from the current race state and simulate the remainder of the race under documented assumptions. Keep the number of future stops configurable but bounded; don't silently assume a strategy is legal or globally optimal if later stops are outside the search space.

## Build checklist

### Milestone 0 — Repository and environment
- [x] Fix `.gitignore` to ignore the actual cache folder `backend/cache/` (and any other chosen cache paths).
- [x] Confirm `.env` is ignored and `.env.example` contains placeholders only.
- [x] Add runtime/dev dependency manifests and document Python 3.14 as the reference version. (Exact pins/lockfile remain a release-hardening task after a clean environment install.)
- [x] Add package markers and standardize the database initialization command.
- [x] Add a README with setup, database creation, connection/schema checks, project architecture and milestones.
- [x] Add config tests and a standalone live PostgreSQL connection check. (Execution on the developer machine remains to be verified.)

**Exit condition:** a clean environment can install dependencies and initialize the schema using documented commands.

**Current progress (2026-11-01):** the user verified the local environment on macOS with Python 3.14.5. Dependency installation reported requirements already satisfied; `python -m pytest -q` passed (3 tests); `database.check_connection` returned OK; `database.init_db` initialized 18 statements; and `psql \dt` showed all 11 expected tables. Milestone 0's exit checks are satisfied for the reported local environment.

### Milestone 1 — Database and one-season ingestion
- [x] Correct the init script usage comment and standardize module invocation.
- [x] Add the schedule/event/session ingestion script and mocked tests; locally verified 22 events and 110 sessions for 2023, with idempotent upserts.
- [x] Add OpenF1-based race-lap ingestion and mocked tests on `feat/race-lap-ingestion`.
- [x] Run the expanded lap-loader tests locally (22 passed before pit-stop-loader tests were added) and initialize the lap metadata schema.
- [x] Dry-run and ingest Bahrain 2023; verify the 1,058-row upsert is idempotent (1,058 total rows and 1,058 unique identities after a repeat run).
- [x] Ingest all 22 2023 Race sessions from OpenF1: 24,479 lap rows processed.
- [x] Validate all race groups in PostgreSQL: `lap_rows` matched `unique_laps` in all 22 races; there are 118 missing lap times and 92 each with missing compound, tyre age, and stint number.
- [x] Capture compound, stint number, and derived tyre age in lap-level data where OpenF1 provides them.
- [x] Add Jolpica historical pit-stop ingestion with dry-run support, mocked tests, and documented source-duration semantics; local tests and live dry-run are pending.
- [ ] Ingest the 2023 pit-stop records and verify driver foreign keys, missing values, row counts, and repeat-run idempotency.
- [ ] Implement weather ingestion.
- [ ] Implement track-status-to-lap mapping for SC/VSC/red-flag periods, including a documented mid-lap policy.
- [ ] Populate and validate the separate `stints` table if it adds reliable value beyond lap-level tyre compound, stint number, and tyre age.
- [ ] Verify every upsert updates all intended fields and run a full-season repeat test.

**Exit condition:** schedule, lap and pit-stop ingestion are repeatable; raw values, nulls, units and failures are documented; source-to-database checks pass.

### Milestone 2 — Data quality and modelling dataset
- [ ] Build explicit clean-lap filtering rules.
- [ ] Produce an attrition report showing counts removed by reason.
- [ ] Verify tyre compound and age sequences per stint.
- [ ] Verify pit-stop lap/duration consistency.
- [ ] Check SC/VSC/red-flag periods against track status.
- [ ] Plot lap time vs tyre age by compound and circuit.
- [ ] Establish season/race holdout split before fitting models.

**Exit condition:** clean dataset is reproducible, interpretable and has enough observations in supported compound/circuit groups.

### Milestone 3 — Degradation model
- [ ] Fit quadratic/piecewise regression baseline.
- [ ] Compare circuit-specific effects vs pooled/circuit-type effects where sparse.
- [ ] Fit gradient-boosted regressor.
- [ ] Evaluate held-out race RMSE and MAE.
- [ ] Inspect residuals, implausible predictions, and predictions at high tyre ages.
- [ ] Persist model artifact and feature/data version metadata.

**Exit condition:** the advanced model is adopted only if held-out results and diagnostics justify it.

### Milestone 4 — Race simulator
- [ ] Define a typed race-state object (lap, laps remaining, compound, tyre age, weather assumption).
- [ ] Predict each remaining lap time from the degradation model.
- [ ] Add pit-lane loss and compound-change transitions.
- [ ] Add SC/VSC event start and duration sampling.
- [ ] Add a separately defined red-flag treatment.
- [ ] Use deterministic seeded scenarios in tests.
- [ ] Unit-test boundary cases: race ends, pit on current lap, no stop possible, overlapping/invalid events.

**Exit condition:** deterministic test scenarios have hand-checkable expected outcomes; stochastic runs reproduce with the same seed.

### Milestone 5 — Strategy optimizer
- [ ] Enumerate feasible candidate pit laps and next compounds.
- [ ] Enforce explicit tyre/stop constraints where supported.
- [ ] Score each candidate on the same Monte Carlo scenarios.
- [ ] Return best expected remaining time plus uncertainty and runner-up alternatives.
- [ ] Explain “pit now” versus selected later lap.
- [ ] Benchmark number of candidates × simulations and end-to-end runtime.
- [ ] Only then evaluate dynamic programming if justified.

**Exit condition:** toy cases produce known optimal choices and small-case results agree between brute force and any later DP solver.

### Milestone 6 — API and frontend
- [ ] Add `/health`.
- [ ] Add `/predict-laptime`.
- [ ] Add `/simulate-strategy`.
- [ ] Add `/optimize-pitstop`.
- [ ] Validate inputs and handle unsupported/missing data gracefully.
- [ ] Add React race-state form and ranked recommendation view.
- [ ] Display assumptions, expected time, uncertainty, comparison strategies, and warnings.
- [ ] Test API contracts and frontend loading/error states.

**Exit condition:** a user can choose a supported race state and see a reproducible, explained recommendation.

### Milestone 7 — Backtesting and reporting
- [ ] Define races held out from training.
- [ ] Compare against actual strategy and at least one simple baseline (e.g., fixed pit window or deterministic no-SC plan).
- [ ] Evaluate model errors separately from decision outcomes.
- [ ] Use paired scenarios when comparing strategies.
- [ ] Report mean deltas, standard errors/confidence intervals, and failure cases.
- [ ] Conduct ablations: no-SC vs SC-aware, regression vs gradient boosting, and per-circuit vs pooled fitting.
- [ ] Document known limitations and reproducibility instructions.

**Exit condition:** every headline claim is backed by a repeatable experiment, and negative or inconclusive outcomes are reported honestly.

### Milestone 8 — Packaging and final polish
- [ ] Add CI for linting and tests.
- [ ] Add Docker only after local setup is reliable.
- [ ] Add sample config and no-secret demo mode.
- [ ] Add architecture diagram and model assumptions page.
- [ ] Prepare demo and conference-paper results from actual reproducible runs.

## Immediate next task

The user has locally verified the schedule and full 2023 race-lap ingestion. The lap loader processed 24,479 rows across 22 races, and the season-wide validation query reported equal `lap_rows` and `unique_laps` for every round. Bahrain re-run idempotency is verified. Missing source values remain nullable: 118 lap times and 92 each for compound, tyre age and stint number.

The first pit-stop loader attempt used OpenF1's `/pit` endpoint, but the live API returned HTTP 404 for both the Bahrain session key (7953) and meeting key (1141). The pit-stop loader has therefore been switched to Jolpica's Ergast-compatible historical `pitstops` and `results` endpoints, which return the race's stop records and provide driver-ID-to-code mappings. This source switch and the new tests have **not yet been run locally**.

The pit-stop duration is preserved from the historical source and converted to milliseconds. It must not be treated as incremental pit loss versus green-flag racing pace; source durations may include entry/exit and, in unusual circumstances, red-flag delay.

Next, on the user's Mac:
1. Pull the latest `feat/race-lap-ingestion` branch.
2. Run `python -m pytest -q` and share any failure output.
3. Run `cd backend && python -m database.init_db && cd ..` to verify the updated schema comment.
4. Run `python -m backend.ingestion.fetch_pit_stops 2023 --round 1 --dry-run`.
5. If the dry-run matches a credible Bahrain pit-stop list, ingest the round without `--dry-run`, query the row count and foreign-key mappings, and repeat once to verify idempotency.
6. Only then consider the full 2023 pit-stop ingestion.

Do not merge the pull request until the pit-stop loader has passed local tests, a live one-round dry-run, one-round write verification and idempotency. The Jolpica client spaces API requests conservatively.

## Research notes

External sources show that F1 pit-stop optimization itself is not a new contribution: a 2024 *European Journal of Operational Research* paper models two-driver strategic interaction with dynamic programming and game theory, and a 2026 *Machine Learning* paper studies reinforcement-learning tactics. Our defensible project contribution should therefore emphasize a clearly scoped open-data implementation, transparent assumptions, reproducible evaluation, and a traceable end-to-end pipeline rather than claiming to invent pit-stop optimization.

- FastF1 data reference: https://github.com/theOehrly/Fast-F1/blob/main/docs/data_reference/index.rst (documents timing/laps and track-status data)
- Aguad & Thraves (2024), *Optimizing pit stop strategies in Formula 1 with dynamic programming and game theory*: https://doi.org/10.1016/j.ejor.2024.07.011
- Thomas et al. (2026), *Race Strategy Reinforcement Learning: Optimising Pitstop Strategy with Emergent Tactics in Formula One*: https://doi.org/10.1007/s10994-026-07081-3
- scikit-learn GroupKFold reference: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html
- Example of stochastic tyre, safety-car and Monte Carlo modelling: https://github.com/LoreMonti/f1-strategy-engine

These are design references, not evidence that this project's implementation or results are already validated. Do not copy code without reviewing its licence and methodology.
