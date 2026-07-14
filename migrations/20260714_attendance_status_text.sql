-- Convert attendance.status from boolean to text values:
-- true  -> "present"
-- false -> "absent"
-- New allowed value: "excused"

ALTER TABLE attendance
  ALTER COLUMN status DROP DEFAULT;

ALTER TABLE attendance
  ALTER COLUMN status TYPE varchar(16)
  USING (
    CASE
      WHEN status IS TRUE THEN 'present'
      ELSE 'absent'
    END
  );

ALTER TABLE attendance
  ALTER COLUMN status SET DEFAULT 'absent',
  ALTER COLUMN status SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'attendance_status_check'
  ) THEN
    ALTER TABLE attendance
      ADD CONSTRAINT attendance_status_check
      CHECK (status IN ('present', 'absent', 'excused'));
  END IF;
END $$;
