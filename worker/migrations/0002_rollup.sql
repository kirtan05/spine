-- spine — rollup targets (PRD Phase 4).
--
-- `confidence` is part of the primary key of every aggregate table on purpose:
-- there is no row shape that can hold measured and modelled time summed together,
-- so no query can accidentally produce one.

-- Resolved per-book state, maintained on push rather than in the nightly rollup.
-- The protocol has no "finished" event, so it is defined here as the resolved
-- percentage crossing FINISHED_THRESHOLD from below. A nightly snapshot would
-- miss a crossing that reverses before the cron runs (appendices, re-reads).
CREATE TABLE book_status (
  doc_hash          TEXT PRIMARY KEY,
  percentage        REAL NOT NULL,
  device            TEXT,               -- winning device name, for the private view only
  updated_at        INTEGER NOT NULL,
  finished_at       INTEGER,            -- first crossing, never overwritten
  last_finished_at  INTEGER,            -- most recent crossing
  finish_count      INTEGER NOT NULL DEFAULT 0
);

-- Daily totals bucketed on Asia/Kolkata calendar dates, not UTC.
CREATE TABLE daily_stats (
  day         TEXT NOT NULL,      -- 'YYYY-MM-DD' in IST
  confidence  TEXT NOT NULL,
  seconds     INTEGER NOT NULL,
  pages       INTEGER NOT NULL,
  sessions    INTEGER NOT NULL,
  books       INTEGER NOT NULL,
  PRIMARY KEY (day, confidence)
);

CREATE TABLE book_stats (
  doc_hash    TEXT NOT NULL,
  confidence  TEXT NOT NULL,
  seconds     INTEGER NOT NULL,
  pages       INTEGER NOT NULL,
  sessions    INTEGER NOT NULL,
  first_read  INTEGER,
  last_read   INTEGER,
  PRIMARY KEY (doc_hash, confidence)
);

-- Scalars the rollup computes that do not fit a table: streaks, last run.
CREATE TABLE rollup_meta (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

CREATE INDEX idx_book_status_finished ON book_status(finished_at);
CREATE INDEX idx_book_status_updated ON book_status(updated_at);
CREATE INDEX idx_progress_updated ON progress(updated_at);
