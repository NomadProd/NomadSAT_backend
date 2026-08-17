-- Soft-delete diagnostic questions so answered items can leave the bank
-- without breaking historical or in-progress attempts. Freeze each attempt's
-- question set in question_ids.

ALTER TABLE diagnostic_questions
  ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

ALTER TABLE diagnostic_attempts
  ADD COLUMN IF NOT EXISTS question_ids jsonb;

DROP INDEX IF EXISTS uq_diagnostic_questions_order_index;
CREATE UNIQUE INDEX uq_diagnostic_questions_order_index
  ON diagnostic_questions (order_index)
  WHERE deleted_at IS NULL;

DROP INDEX IF EXISTS uq_diagnostic_questions_question_url;
CREATE UNIQUE INDEX uq_diagnostic_questions_question_url
  ON diagnostic_questions (question_url)
  WHERE deleted_at IS NULL AND question_url IS NOT NULL;
