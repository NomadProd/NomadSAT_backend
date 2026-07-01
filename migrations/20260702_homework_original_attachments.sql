-- Archive homework attachments when admin returns for revision.
-- Student edits affect attachments only; original_attachments is immutable.

ALTER TABLE homework_results
  ADD COLUMN IF NOT EXISTS original_attachments jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Backfill: copy current attachments for rows already pending revision.
UPDATE homework_results
SET original_attachments = attachments
WHERE returned_at IS NOT NULL
  AND original_attachments = '[]'::jsonb
  AND attachments != '[]'::jsonb;
