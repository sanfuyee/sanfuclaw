-- Scheduled tasks: cron-driven synthetic envelopes that fire into a target channel.

CREATE TABLE IF NOT EXISTS schedules (
    id              TEXT PRIMARY KEY,
    cron            TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    target_channel  TEXT NOT NULL,
    target_session  TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT NOT NULL DEFAULT '',
    next_run_at     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

-- Scheduler loop wakes up to find the earliest enabled run; index covers it.
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled, next_run_at);
