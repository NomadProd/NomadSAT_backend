-- Full submission snapshots preserved when admin returns homework for revision.

ALTER TABLE homework_results
  ADD COLUMN IF NOT EXISTS submission_history jsonb NOT NULL DEFAULT '[]'::jsonb;
