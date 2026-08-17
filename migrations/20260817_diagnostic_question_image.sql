-- Figure/image for a diagnostic question (separate from the unique SAT question_url).
ALTER TABLE diagnostic_questions
  ADD COLUMN IF NOT EXISTS question_image text,
  ADD COLUMN IF NOT EXISTS question_image_public_id text;
