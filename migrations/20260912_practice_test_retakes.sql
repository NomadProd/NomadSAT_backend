-- Allow a student more than one attempt at a practice test.
--
-- The unique index forbade a retake outright, which left students whose
-- attempt was interrupted with no way to sit the test again without deleting
-- the score they already had. Attempts are now history: every one is kept, and
-- the API refuses only a second attempt while one is still in progress.

DROP INDEX IF EXISTS uq_practice_test_attempts_test_student;

CREATE INDEX IF NOT EXISTS idx_practice_test_attempts_test_student
    ON practice_test_attempts (test_id, student_id);
