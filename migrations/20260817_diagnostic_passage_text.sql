ALTER TABLE diagnostic_questions
  ADD COLUMN IF NOT EXISTS passage_text text;
