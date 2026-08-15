-- Digital SAT diagnostic test (standalone; not tied to assignments/homework/mock_results).
-- official_score (and similar calibration fields) can be added later as nullable columns
-- on diagnostic_attempts without changing the attempt/answer relationship.

CREATE TABLE diagnostic_questions (
  id              bigserial PRIMARY KEY,
  section         varchar NOT NULL CHECK (section IN ('reading_writing', 'math')),
  domain          varchar NOT NULL,
  difficulty      varchar NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard')),
  points          integer NOT NULL,
  order_index     integer NOT NULL,
  question_text   text NOT NULL,
  question_image  text,
  choices         jsonb NOT NULL,
  correct_choice  varchar NOT NULL,
  explanation     text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  created_by_id   bigint REFERENCES users(id)
);

CREATE UNIQUE INDEX uq_diagnostic_questions_order_index
  ON diagnostic_questions(order_index);

CREATE INDEX idx_diagnostic_questions_order
  ON diagnostic_questions(order_index);

CREATE TABLE diagnostic_attempts (
  id                    bigserial PRIMARY KEY,
  student_id            bigint NOT NULL REFERENCES users(id),
  started_at            timestamptz NOT NULL DEFAULT now(),
  completed_at          timestamptz,
  rw_points             integer,
  math_points           integer,
  rw_scaled_estimate    integer,
  math_scaled_estimate  integer,
  total_point_estimate  integer,
  total_range_low       integer,
  total_range_high      integer,
  status                varchar NOT NULL DEFAULT 'in_progress'
                          CHECK (status IN ('in_progress', 'completed', 'abandoned'))
);

CREATE INDEX idx_diagnostic_attempts_student_id
  ON diagnostic_attempts(student_id);

CREATE TABLE diagnostic_answers (
  id              bigserial PRIMARY KEY,
  attempt_id      bigint NOT NULL REFERENCES diagnostic_attempts(id) ON DELETE CASCADE,
  question_id     bigint NOT NULL REFERENCES diagnostic_questions(id),
  selected_choice varchar,
  is_correct      boolean,
  answered_at     timestamptz
);

CREATE UNIQUE INDEX uq_diagnostic_answers_attempt_question
  ON diagnostic_answers(attempt_id, question_id);

CREATE INDEX idx_diagnostic_answers_attempt_id
  ON diagnostic_answers(attempt_id);
