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
15. **Scope limits:** version 1 is a single-driver strategy support model, not an official team simulator. Traffic, overtaking, multi-car undercut/overcut games, wet-weather transitions, regulations/mandatory compound constraints, and live ingestion are later scope unless a hard project requirement says otherwise.

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

External examples show that open F1 strategy projects use empirically calibrated tyre models, stochastic neutralisation events, paired Monte Carlo scenarios, and held-out validation. These are useful design precedents, not proof that this project's outputs are already accurate:
- FastF1 API/source: https://github.com/theOehrly/Fast-F1
- Open race-strategy modelling example with stochastic safety-car treatment: https://github.com/vidyuthdev/f1-2026-race-strategy
- Strategy engine describing paired scenarios and explicit simplifications: https://github.com/LoreMonti/f1-strategy-engine
- Race-strategy Monte Carlo project with evaluation and uncertainty reporting: https://github.com/Hellblazer704/pitwall

Do not copy another project's claims, data or code without reviewing its license and methodology. Our implementation should be independently tested against our own data and stated assumptions.
