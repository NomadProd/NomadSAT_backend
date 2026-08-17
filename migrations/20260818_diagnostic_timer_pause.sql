-- Freeze the diagnostic section clock while the student is away from the test.
-- timer_paused_at marks when they left.
-- timer_pause_seconds is accumulated away time.

ALTER TABLE diagnostic_attempts
  ADD COLUMN IF NOT EXISTS timer_paused_at timestamptz;

ALTER TABLE diagnostic_attempts
  ADD COLUMN IF NOT EXISTS timer_pause_seconds integer NOT NULL DEFAULT 0;
