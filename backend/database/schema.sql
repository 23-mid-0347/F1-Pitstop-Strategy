CREATE TABLE IF NOT EXISTS drivers (
    driver_id       SERIAL PRIMARY KEY,
    driver_code     TEXT UNIQUE NOT NULL,
    full_name       TEXT NOT NULL,
    nationality     TEXT,
    date_of_birth   DATE
);

CREATE TABLE IF NOT EXISTS constructors (
    constructor_id  SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    nationality     TEXT
);

CREATE TABLE IF NOT EXISTS circuits (
    circuit_id      SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    location        TEXT,
    country         TEXT
);

CREATE TABLE IF NOT EXISTS seasons (
    season_id       SERIAL PRIMARY KEY,
    year            INT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id        SERIAL PRIMARY KEY,
    season_id       INT NOT NULL REFERENCES seasons(season_id),
    round           INT NOT NULL,
    event_name      TEXT NOT NULL,
    circuit_id      INT REFERENCES circuits(circuit_id),
    event_date      DATE,
    UNIQUE (season_id, round)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id      SERIAL PRIMARY KEY,
    event_id        INT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    session_type    TEXT NOT NULL,
    session_date    TIMESTAMP,
    UNIQUE (event_id, session_type)
);

CREATE TABLE IF NOT EXISTS lap_times (
    lap_id          SERIAL PRIMARY KEY,
    session_id      INT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    driver_id       INT NOT NULL REFERENCES drivers(driver_id),
    lap_number      INT NOT NULL,
    lap_time_ms     INT,
    tire_compound   TEXT,
    UNIQUE (session_id, driver_id, lap_number)
);

CREATE TABLE IF NOT EXISTS pit_stops (
    pit_stop_id     SERIAL PRIMARY KEY,
    session_id      INT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    driver_id       INT NOT NULL REFERENCES drivers(driver_id),
    stop_number     INT NOT NULL,
    lap             INT,
    duration_ms     INT,
    UNIQUE (session_id, driver_id, stop_number)
);

CREATE TABLE IF NOT EXISTS stints (
    stint_id        SERIAL PRIMARY KEY,
    session_id      INT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    driver_id       INT NOT NULL REFERENCES drivers(driver_id),
    stint_number    INT NOT NULL,
    compound        TEXT,
    lap_start       INT,
    lap_end         INT,
    UNIQUE (session_id, driver_id, stint_number)
);

CREATE TABLE IF NOT EXISTS weather (
    weather_id      SERIAL PRIMARY KEY,
    session_id      INT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    recorded_at     TIMESTAMP,
    air_temp        NUMERIC,
    track_temp      NUMERIC,
    humidity        NUMERIC,
    rainfall        BOOLEAN,
    wind_speed      NUMERIC,
    UNIQUE (session_id, recorded_at)
);

-- Central to the pit-stop strategy project, not part of the F1 RAG database.
CREATE TABLE IF NOT EXISTS safety_car_periods (
    period_id       SERIAL PRIMARY KEY,
    session_id      INT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    start_lap       INT NOT NULL,
    end_lap         INT NOT NULL,
    period_type     TEXT NOT NULL   -- 'SC', 'VSC', 'RedFlag'
);

CREATE INDEX IF NOT EXISTS idx_events_season ON events(season_id);
CREATE INDEX IF NOT EXISTS idx_sessions_event ON sessions(event_id);
CREATE INDEX IF NOT EXISTS idx_laptimes_session_driver ON lap_times(session_id, driver_id);
CREATE INDEX IF NOT EXISTS idx_pitstops_session_driver ON pit_stops(session_id, driver_id);
CREATE INDEX IF NOT EXISTS idx_stints_session_driver ON stints(session_id, driver_id);
CREATE INDEX IF NOT EXISTS idx_weather_session ON weather(session_id);
CREATE INDEX IF NOT EXISTS idx_safetycar_session ON safety_car_periods(session_id);

-- Preserve schedule fields that FastF1 supplies before a formal circuit name is
-- resolved. These ALTERs also upgrade databases initialized from an older schema.
ALTER TABLE events ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS location TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS official_event_name TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS event_format TEXT;


-- Preserve source tyre-age, pit-lap, track-status and quality indicators for modelling.
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS stint_number INT;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS tyre_age DOUBLE PRECISION;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS fresh_tyre BOOLEAN;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS pit_in_lap BOOLEAN;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS pit_out_lap BOOLEAN;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS track_status TEXT;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS is_accurate BOOLEAN;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS deleted_reason TEXT;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS fastf1_generated BOOLEAN;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS lap_start_time_ms BIGINT;
ALTER TABLE lap_times ADD COLUMN IF NOT EXISTS lap_end_time_ms BIGINT;
