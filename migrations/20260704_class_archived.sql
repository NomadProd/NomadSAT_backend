-- Soft-archive classes: hidden from non-admins, reversible by admin.

ALTER TABLE classes
  ADD COLUMN IF NOT EXISTS archived boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_classes_archived ON classes (archived);
