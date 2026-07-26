-- spine — core schema (PRD Phase 1).
-- All timestamps are unix seconds. Server-assigned unless noted otherwise.

CREATE TABLE users (
  username    TEXT PRIMARY KEY,
  key_hash    TEXT NOT NULL,      -- HMAC-SHA-256(AUTH_PEPPER, client MD5 key)
  created_at  INTEGER NOT NULL
);

-- doc_hash is KOReader's "binary" document id: MD5 over 1 KiB samples at
-- exponentially spaced offsets (util.partialMD5 in the KOReader tree).
-- See laptop/src/spinecore/partial_md5.py for the ported loop.
CREATE TABLE documents (
  doc_hash      TEXT PRIMARY KEY,
  title         TEXT,
  authors       TEXT,
  series        TEXT,
  series_index  REAL,
  filename      TEXT,
  first_seen    INTEGER NOT NULL
);

CREATE TABLE progress (
  doc_hash    TEXT NOT NULL,
  device_id   TEXT NOT NULL,
  device      TEXT,
  progress    TEXT NOT NULL,      -- page number or xpointer, opaque
  percentage  REAL NOT NULL,
  updated_at  INTEGER NOT NULL,   -- server clock, never the device's
  PRIMARY KEY (doc_hash, device_id)
);

CREATE TABLE sessions (
  id           TEXT PRIMARY KEY,  -- deterministic: hash(source, device_id, doc_hash, started_at)
  doc_hash     TEXT,
  title        TEXT,
  authors      TEXT,
  device_id    TEXT,
  started_at   INTEGER NOT NULL,
  ended_at     INTEGER,           -- interval rows set this; point rows leave NULL
  duration_s   INTEGER,
  pages        INTEGER,
  source       TEXT NOT NULL,     -- koreader | kavita | playbooks | wellbeing
  confidence   TEXT NOT NULL,     -- exact | approx | inferred
  method       TEXT,              -- e.g. interval, wellbeing-proportional/monthly
  imported_at  INTEGER NOT NULL
);

CREATE INDEX idx_sessions_started ON sessions(started_at);
CREATE INDEX idx_sessions_doc ON sessions(doc_hash);
