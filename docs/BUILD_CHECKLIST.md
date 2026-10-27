# F1 Pit Stop Strategy Optimizer — Plan Alignment and Build Checklist

Repository: https://github.com/23-mid-0347/F1-Pitstop-Strategy  
Plan reviewed: Project Plan v4  
Review date: 2026-10-27

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

FastF1 -> ingestion and quality checks -> PostgreSQL -> modelling dataset -> tyre model -> stochastic race simulator -> strategy optimizer -> FastAPI -> React interface -> backtest/evaluation.

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
16. **Tyre age must be explicit:** add a per-lap tyre-age field (and preferably source stint number/fresh-tyre indicator) to `lap_times` or the clean modelling dataset. Stint start/end laps alone cannot reliably identify the age of a used tyre fitted during a race. Use FastF1's tyre-life field when available and document missing-value handling.
17. **Pit loss must be calibrated correctly:** `pit_stops.duration_ms` records pit-lane entry-to-exit duration, not the extra time lost versus racing at normal pace. Do not add that raw duration directly to lap-time sums. Estimate circuit/session pit loss as incremental time relative to a green-flag reference, and model the SC/VSC reduction as a separate calibrated assumption.
18. **Legal strategy constraints:** version 1 should expose mandatory compound/stop rules as explicit configurable constraints, using the relevant season's regulations. Never rank an infeasible strategy as optimal. Wet and red-flag cases should be flagged unsupported until their rules are implemented and tested.
19. **V1 recommendation scope:** recommend the next pit decision from the current race state and simulate the remainder of the race under documented assumptions. Keep the number of future stops configurable but bounded; don't silently assume a strategy is legal or globally optimal if later stops are outside the search space.

## Build checklist

### Milestone 0 — Repository and environment
- [ ] Fix `.gitignore` to ignore the actual cache folder `backend/cache/` (and any other chosen cache paths).
- [ ] Confirm `.env` is ignored and `.env.example` contains placeholders only.
- [ ] Add the exact dependency manifest and supported Python version.
- [ ] Ensure package markers and run commands are consistent.
- [ ] Add a README with setup, database creation, ingestion, tests, and architecture.
- [ ] Add a smoke test for configuration and database connection.

**Exit condition:** a clean environment can install dependencies and initialize the schema using documented commands.

### Milestone 1 — Database and one-season ingestion
- [ ] Correct the init script usage comment.
- [ ] Implement schedule/event/session ingestion.
- [ ] Implement lap ingestion, preserving time units and missing values.
- [ ] Implement stint/compound ingestion with reliable tyre-age derivation.
- [ ] Implement pit-stop ingestion and document what duration means.
- [ ] Implement weather ingestion.
- [ ] Implement track-status-to-lap mapping for SC/VSC/red-flag periods, including mid-lap policy.
- [ ] Ensure upserts are idempotent and update every intended field.
- [ ] Run one complete season and verify row counts and foreign-key relationships.

**Exit condition:** re-running ingestion does not duplicate rows; failed sessions are logged; source-to-database checks pass.

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

Start with **Milestone 0 and the initial ingestion smoke test**, not the model. First fix the cache ignore, standardize how modules are run, then create the first ingestion script(s) and load one season. That gives the project a trustworthy data foundation for every later claim.

## Research notes

External sources show that F1 pit-stop optimization itself is not a new contribution: a 2024 *European Journal of Operational Research* paper models two-driver strategic interaction with dynamic programming and game theory, and a 2026 *Machine Learning* paper studies reinforcement-learning tactics. Our defensible project contribution should therefore emphasize a clearly scoped open-data implementation, transparent assumptions, reproducible evaluation, and a traceable end-to-end pipeline rather than claiming to invent pit-stop optimization.

- FastF1 data reference: https://github.com/theOehrly/Fast-F1/blob/main/docs/data_reference/index.rst (documents timing/laps and track-status data)
- Aguad & Thraves (2024), *Optimizing pit stop strategies in Formula 1 with dynamic programming and game theory*: https://doi.org/10.1016/j.ejor.2024.07.011
- Thomas et al. (2026), *Race Strategy Reinforcement Learning: Optimising Pitstop Strategy with Emergent Tactics in Formula One*: https://doi.org/10.1007/s10994-026-07081-3
- scikit-learn GroupKFold reference: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html
- Example of stochastic tyre, safety-car and Monte Carlo modelling: https://github.com/LoreMonti/f1-strategy-engine

These are design references, not evidence that this project's implementation or results are already validated. Do not copy code without reviewing its licence and methodology.
