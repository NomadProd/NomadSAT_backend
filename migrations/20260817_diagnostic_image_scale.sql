ALTER TABLE diagnostic_questions
  ADD COLUMN IF NOT EXISTS image_scale double precision NOT NULL DEFAULT 0.85;
