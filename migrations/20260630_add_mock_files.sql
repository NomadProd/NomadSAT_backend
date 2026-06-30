-- =============================================================================
-- Multi-file mock result attachments
-- =============================================================================
--
-- EXISTING TABLE (DO NOT CREATE, ALTER, OR DROP):
--   mock_results
--     id               int4        PRIMARY KEY
--     assignment_id    int4        NOT NULL  → assignments(id)
--     student_id       int4        NOT NULL  → users(id)
--     submitted        bool        NOT NULL
--     total_points     int4        NULL
--     verbal_points    int4        NULL
--     math_points      int4        NULL
--     verbal_incorrect int4        NULL
--     math_incorrect   int4        NULL
--     weak_areas       text        NULL
--     photo_link       varchar     NULL
--
-- All existing mock_results rows and photo_link values MUST be preserved.
-- This migration only adds mock_files and copies legacy photo_link rows into it.
-- photo_link is never removed or overwritten.
-- =============================================================================

-- Step 1: attachments table (no-op if already exists)
CREATE TABLE IF NOT EXISTS mock_files (
  id           bigserial    PRIMARY KEY,
  result_id    bigint       NOT NULL REFERENCES mock_results(id) ON DELETE CASCADE,
  url          text         NOT NULL,
  public_id    text,                    -- nullable for legacy rows migrated from photo_link
  filename     text         NOT NULL,
  content_type text         NOT NULL,
  size_bytes   bigint       NOT NULL DEFAULT 0,
  uploaded_at  timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mock_files_result_id ON mock_files(result_id);

-- Step 2: migrate legacy single-file proof from mock_results.photo_link → mock_files
-- Idempotent: skips rows that already have at least one mock_files entry for that result.
INSERT INTO mock_files (result_id, url, public_id, filename, content_type, size_bytes)
SELECT
  mr.id,
  mr.photo_link,
  NULL,
  CASE
    WHEN lower(mr.photo_link) LIKE '%.pdf%'  THEN 'legacy-proof.pdf'
    WHEN lower(mr.photo_link) LIKE '%.png%'  THEN 'legacy-proof.png'
    WHEN lower(mr.photo_link) LIKE '%.webp%' THEN 'legacy-proof.webp'
    WHEN lower(mr.photo_link) LIKE '%.gif%'  THEN 'legacy-proof.gif'
    ELSE 'legacy-proof.jpg'
  END,
  CASE
    WHEN lower(mr.photo_link) LIKE '%.pdf%' THEN 'application/pdf'
    ELSE 'image/jpeg'
  END,
  0
FROM mock_results mr
WHERE mr.photo_link IS NOT NULL
  AND btrim(mr.photo_link) <> ''
  AND NOT EXISTS (
    SELECT 1 FROM mock_files mf WHERE mf.result_id = mr.id
  );

-- Step 3: RLS skipped — FastAPI uses direct DB + JWT auth (users.id is bigint, not auth.uid() UUID).
-- Apply Supabase RLS separately if direct client access is added later.
