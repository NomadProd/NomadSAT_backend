-- Practice tests: in-app, auto-scored SAT practice tests authored by staff and
-- published to classes. Standalone -- shares nothing with the paper-mock flow
-- (mock_results/assignments) or with the hardcoded diagnostic test.
--
-- v1 is non-adaptive: one reading_writing module and one math module per test.
-- Adaptive (3 module-2 variants per section) lands later as a `variant` column on
-- practice_test_modules plus extra rows. No other table changes.

CREATE TABLE IF NOT EXISTS practice_tests (
  id             bigserial PRIMARY KEY,
  title          varchar NOT NULL,
  description    text,
  visible        boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now(),
  created_by_id  bigint REFERENCES users(id),
  deleted_at     timestamptz
);

CREATE TABLE IF NOT EXISTS practice_test_modules (
  id                       bigserial PRIMARY KEY,
  test_id                  bigint NOT NULL REFERENCES practice_tests(id) ON DELETE CASCADE,
  section                  varchar NOT NULL CHECK (section IN ('reading_writing', 'math')),
  order_index              integer NOT NULL,
  time_limit_seconds       integer NOT NULL,
  required_question_count  integer NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_test_modules_order
  ON practice_test_modules(test_id, order_index);

CREATE INDEX IF NOT EXISTS idx_practice_test_modules_test_id
  ON practice_test_modules(test_id);

CREATE TABLE IF NOT EXISTS practice_test_questions (
  id                       bigserial PRIMARY KEY,
  module_id                bigint NOT NULL REFERENCES practice_test_modules(id) ON DELETE CASCADE,
  order_index              integer NOT NULL,
  domain                   varchar NOT NULL,
  difficulty               varchar NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard')),
  passage_text             text,
  question_text            text NOT NULL,
  explanation              text,
  question_image           text,
  question_image_public_id text,
  image_scale              double precision NOT NULL DEFAULT 0.85,
  answer_type              varchar NOT NULL CHECK (answer_type IN ('mcq', 'spr')),
  choices                  jsonb,
  correct_choice           varchar,
  correct_answers          jsonb,
  created_at               timestamptz NOT NULL DEFAULT now(),
  created_by_id            bigint REFERENCES users(id),
  deleted_at               timestamptz,

  -- An mcq carries choices + one correct key; an spr carries a list of accepted
  -- answers and no choices. Enforced here so no route can write a half-shaped question.
  CONSTRAINT ck_practice_test_questions_answer_shape CHECK (
    (answer_type = 'mcq'
       AND choices IS NOT NULL
       AND correct_choice IS NOT NULL
       AND correct_answers IS NULL)
    OR
    (answer_type = 'spr'
       AND correct_answers IS NOT NULL
       AND choices IS NULL
       AND correct_choice IS NULL)
  )
);

-- order_index is unique per module among live questions only, so a soft-deleted
-- question frees its slot (same trick as diagnostic_questions).
CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_test_questions_order
  ON practice_test_questions(module_id, order_index)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_practice_test_questions_module_id
  ON practice_test_questions(module_id);

CREATE TABLE IF NOT EXISTS practice_test_classes (
  test_id   bigint NOT NULL REFERENCES practice_tests(id) ON DELETE CASCADE,
  class_id  integer NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
  PRIMARY KEY (test_id, class_id)
);

CREATE INDEX IF NOT EXISTS idx_practice_test_classes_class_id
  ON practice_test_classes(class_id);

CREATE TABLE IF NOT EXISTS practice_test_attempts (
  id                   bigserial PRIMARY KEY,
  test_id              bigint NOT NULL REFERENCES practice_tests(id) ON DELETE CASCADE,
  student_id           bigint NOT NULL REFERENCES users(id),
  status               varchar NOT NULL DEFAULT 'in_progress'
                         CHECK (status IN ('in_progress', 'completed', 'abandoned')),
  started_at           timestamptz NOT NULL DEFAULT now(),
  completed_at         timestamptz,
  current_module_id    bigint REFERENCES practice_test_modules(id),
  current_question_id  bigint REFERENCES practice_test_questions(id),
  module_started_at    timestamptz,
  timer_paused_at      timestamptz,
  timer_pause_seconds  integer NOT NULL DEFAULT 0,
  question_ids         jsonb,
  rw_raw               integer,
  math_raw             integer,
  rw_scaled            integer,
  math_scaled          integer,
  total_scaled         integer
);

-- One attempt per student per test. This constraint is the rule, not route code.
CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_test_attempts_test_student
  ON practice_test_attempts(test_id, student_id);

CREATE INDEX IF NOT EXISTS idx_practice_test_attempts_student_id
  ON practice_test_attempts(student_id);

CREATE TABLE IF NOT EXISTS practice_test_answers (
  id              bigserial PRIMARY KEY,
  attempt_id      bigint NOT NULL REFERENCES practice_test_attempts(id) ON DELETE CASCADE,
  question_id     bigint NOT NULL REFERENCES practice_test_questions(id),
  selected_choice varchar,
  response_text   text,
  is_correct      boolean,
  answered_at     timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_test_answers_attempt_question
  ON practice_test_answers(attempt_id, question_id);

CREATE INDEX IF NOT EXISTS idx_practice_test_answers_attempt_id
  ON practice_test_answers(attempt_id);
