-- Step 1: new attachments table
CREATE TABLE homework_files (
  id           bigserial    PRIMARY KEY,
  result_id    bigint       NOT NULL REFERENCES homework_results(id) ON DELETE CASCADE,
  url          text         NOT NULL,
  public_id    text,              -- Cloudinary public_id; nullable (legacy rows have none)
  filename     text         NOT NULL,
  content_type text         NOT NULL,
  size_bytes   bigint       NOT NULL,
  uploaded_at  timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX idx_homework_files_result_id ON homework_files(result_id);

-- Step 2: new columns on homework_results for return-for-revision flow
ALTER TABLE homework_results
  ADD COLUMN IF NOT EXISTS returned_at     timestamptz,
  ADD COLUMN IF NOT EXISTS returned_by_id  bigint REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS return_reason   text;

-- DO NOT DROP OR MODIFY photo_link — it must remain for legacy records.

-- Step 3: Supabase RLS policies for homework_files
ALTER TABLE homework_files ENABLE ROW LEVEL SECURITY;

-- Admin: full access
CREATE POLICY "admin_all_homework_files" ON homework_files
  FOR ALL USING (
    EXISTS (SELECT 1 FROM users WHERE id = auth.uid()::bigint AND role = 'admin')
  );

-- Student: only their own result's files
CREATE POLICY "student_own_homework_files" ON homework_files
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM homework_results hr
      JOIN assignments a ON a.id = hr.assignment_id
      WHERE hr.id = homework_files.result_id
        AND a.student_id = auth.uid()::bigint
    )
  );

-- Teacher: files for results in their sessions
CREATE POLICY "teacher_own_class_homework_files" ON homework_files
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM homework_results hr
      JOIN assignments a ON a.id = hr.assignment_id
      JOIN sessions s ON s.id = a.session_id
      WHERE hr.id = homework_files.result_id
        AND s.teacher_id = auth.uid()::bigint
    )
  );
