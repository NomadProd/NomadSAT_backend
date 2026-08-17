-- Persist Math module start and the last viewed question so Continue restores
-- remaining time and the exact item the student left on.

ALTER TABLE diagnostic_attempts
  ADD COLUMN IF NOT EXISTS math_started_at timestamptz;

ALTER TABLE diagnostic_attempts
  ADD COLUMN IF NOT EXISTS current_question_id bigint
    REFERENCES diagnostic_questions(id);
