-- question_image was a unique SAT-style question URL, not an image.
ALTER TABLE diagnostic_questions
  RENAME COLUMN question_image TO question_url;

UPDATE diagnostic_questions
SET question_url = NULL
WHERE question_url IS NOT NULL AND btrim(question_url) = '';

CREATE UNIQUE INDEX uq_diagnostic_questions_question_url
  ON diagnostic_questions (question_url)
  WHERE question_url IS NOT NULL;
