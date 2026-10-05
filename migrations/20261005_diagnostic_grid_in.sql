-- Grid-in (student-produced response) questions in the diagnostic Math section.
-- Same shape as practice_test_questions: an mcq carries choices + one correct
-- key, an spr carries a list of accepted answers and no choices. Existing rows
-- default to mcq and already satisfy the constraint.

ALTER TABLE diagnostic_questions
  ADD COLUMN IF NOT EXISTS answer_type text NOT NULL DEFAULT 'mcq',
  ADD COLUMN IF NOT EXISTS correct_answers jsonb,
  ALTER COLUMN choices DROP NOT NULL,
  ALTER COLUMN correct_choice DROP NOT NULL;

ALTER TABLE diagnostic_questions
  DROP CONSTRAINT IF EXISTS ck_diagnostic_questions_answer_shape;
ALTER TABLE diagnostic_questions
  ADD CONSTRAINT ck_diagnostic_questions_answer_shape CHECK (
    (answer_type = 'mcq'
       AND choices IS NOT NULL
       AND correct_choice IS NOT NULL
       AND correct_answers IS NULL)
    OR
    (answer_type = 'spr'
       AND section = 'math'
       AND correct_answers IS NOT NULL
       AND choices IS NULL
       AND correct_choice IS NULL)
  );

-- The student's raw grid-in input, stored verbatim.
ALTER TABLE diagnostic_answers
  ADD COLUMN IF NOT EXISTS response_text text;
