-- The practice-test format moved from one module per section to the official
-- two, so a test is R&W 1-2 then Math 1-2 (98 questions). Tests created before
-- this change hold only two modules; this backfills them.
--
-- Order matters: the existing Math module moves from order_index 2 to 3 before
-- the second R&W module can take slot 2, or the unique (test_id, order_index)
-- index rejects the insert.

UPDATE practice_test_modules
   SET order_index = 3
 WHERE section = 'math'
   AND order_index = 2
   AND test_id IN (
     SELECT test_id FROM practice_test_modules
      GROUP BY test_id HAVING count(*) = 2
   );

INSERT INTO practice_test_modules
  (test_id, section, order_index, time_limit_seconds, required_question_count)
SELECT t.id, 'reading_writing', 2, 1920, 27
  FROM practice_tests t
 WHERE NOT EXISTS (
   SELECT 1 FROM practice_test_modules m
    WHERE m.test_id = t.id AND m.order_index = 2
 );

INSERT INTO practice_test_modules
  (test_id, section, order_index, time_limit_seconds, required_question_count)
SELECT t.id, 'math', 4, 2100, 22
  FROM practice_tests t
 WHERE NOT EXISTS (
   SELECT 1 FROM practice_test_modules m
    WHERE m.test_id = t.id AND m.order_index = 4
 );

-- A test built for the old format cannot be complete under the new one.
UPDATE practice_tests SET visible = false
 WHERE visible = true
   AND id IN (
     SELECT m.test_id
       FROM practice_test_modules m
       LEFT JOIN practice_test_questions q
         ON q.module_id = m.id AND q.deleted_at IS NULL
      GROUP BY m.test_id, m.id, m.required_question_count
     HAVING count(q.id) <> m.required_question_count
   );
