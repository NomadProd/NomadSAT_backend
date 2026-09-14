-- Move the practice-test module clock onto the server.
--
-- The countdown used to be the browser's opinion: it diffed the device clock
-- against module_started_at, and nothing here ever checked the time. A device
-- running slow was handed free minutes, and an answer could arrive long after
-- its module ended.
--
-- The server now owns the clock, and it needs to know when it last heard from
-- the student. The taking screen heartbeats while the test is open; silence
-- longer than the grace is the student having gone, and that gap is banked
-- into timer_pause_seconds so it does not count against them. That single
-- signal replaces both of the old pause mechanisms: switching tab keeps
-- reporting and so keeps counting, while closing the tab goes quiet and stops
-- the clock.
--
-- Existing attempts are backfilled to the moment this runs, not to their module
-- start: anyone actually sitting a test as this deploys would otherwise have
-- their whole elapsed time read as an absence and handed straight back to them.
--
-- Written to be re-runnable, so applying it over an earlier partial run lands
-- in the same place.

ALTER TABLE practice_test_attempts
    ADD COLUMN IF NOT EXISTS last_seen_at timestamptz;

UPDATE practice_test_attempts
   SET last_seen_at = now()
 WHERE last_seen_at IS NULL;

ALTER TABLE practice_test_attempts
    ALTER COLUMN last_seen_at SET DEFAULT now();

ALTER TABLE practice_test_attempts
    ALTER COLUMN last_seen_at SET NOT NULL;

-- timer_paused_at is deliberately left in place. Nothing writes it any more,
-- but it is the record of how attempts taken under the old rules were timed.
