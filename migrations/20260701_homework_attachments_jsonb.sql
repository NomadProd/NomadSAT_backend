-- =============================================================================
-- Store file attachments as JSONB on homework_results / mock_results.
-- Drops legacy homework_files and mock_files tables after migrating their rows.
-- photo_link is never removed or modified on either parent table.
-- =============================================================================

-- Step 2: JSONB attachments column (before data migration)
ALTER TABLE homework_results
  ADD COLUMN IF NOT EXISTS attachments jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE mock_results
  ADD COLUMN IF NOT EXISTS attachments jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Step 4: return-for-revision columns on homework_results only
ALTER TABLE homework_results
  ADD COLUMN IF NOT EXISTS returned_at     timestamptz,
  ADD COLUMN IF NOT EXISTS returned_by_id  bigint REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS return_reason   text;

-- Migrate homework_files → homework_results.attachments (preserves row ids)
DO $$
BEGIN
  IF to_regclass('public.homework_files') IS NOT NULL THEN
    UPDATE homework_results hr
    SET attachments = sub.files
    FROM (
      SELECT
        hf.result_id,
        COALESCE(
          jsonb_agg(
            jsonb_build_object(
              'id', hf.id,
              'url', hf.url,
              'public_id', hf.public_id,
              'filename', hf.filename,
              'content_type', hf.content_type,
              'size_bytes', hf.size_bytes,
              'uploaded_at', to_char(hf.uploaded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
            )
            ORDER BY hf.uploaded_at ASC, hf.id ASC
          ),
          '[]'::jsonb
        ) AS files
      FROM homework_files hf
      GROUP BY hf.result_id
    ) sub
    WHERE hr.id = sub.result_id;
  END IF;
END $$;

-- Migrate mock_files → mock_results.attachments (preserves row ids)
DO $$
BEGIN
  IF to_regclass('public.mock_files') IS NOT NULL THEN
    UPDATE mock_results mr
    SET attachments = sub.files
    FROM (
      SELECT
        mf.result_id,
        COALESCE(
          jsonb_agg(
            jsonb_build_object(
              'id', mf.id,
              'url', mf.url,
              'public_id', mf.public_id,
              'filename', mf.filename,
              'content_type', mf.content_type,
              'size_bytes', mf.size_bytes,
              'uploaded_at', to_char(mf.uploaded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
            )
            ORDER BY mf.uploaded_at ASC, mf.id ASC
          ),
          '[]'::jsonb
        ) AS files
      FROM mock_files mf
      GROUP BY mf.result_id
    ) sub
    WHERE mr.id = sub.result_id;
  END IF;
END $$;

-- Legacy photo_link → single attachment when attachments still empty
UPDATE homework_results hr
SET attachments = jsonb_build_array(
  jsonb_build_object(
    'id', hr.id * 1000000,
    'url', hr.photo_link,
    'public_id', NULL,
    'filename', CASE
      WHEN lower(hr.photo_link) LIKE '%.pdf%'  THEN 'legacy-proof.pdf'
      WHEN lower(hr.photo_link) LIKE '%.png%'  THEN 'legacy-proof.png'
      WHEN lower(hr.photo_link) LIKE '%.webp%' THEN 'legacy-proof.webp'
      WHEN lower(hr.photo_link) LIKE '%.gif%'  THEN 'legacy-proof.gif'
      ELSE 'legacy-proof.jpg'
    END,
    'content_type', CASE
      WHEN lower(hr.photo_link) LIKE '%.pdf%' THEN 'application/pdf'
      ELSE 'image/jpeg'
    END,
    'size_bytes', 0,
    'uploaded_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
  )
)
WHERE hr.photo_link IS NOT NULL
  AND btrim(hr.photo_link) <> ''
  AND hr.attachments = '[]'::jsonb;

UPDATE mock_results mr
SET attachments = jsonb_build_array(
  jsonb_build_object(
    'id', mr.id * 1000000,
    'url', mr.photo_link,
    'public_id', NULL,
    'filename', CASE
      WHEN lower(mr.photo_link) LIKE '%.pdf%'  THEN 'legacy-proof.pdf'
      WHEN lower(mr.photo_link) LIKE '%.png%'  THEN 'legacy-proof.png'
      WHEN lower(mr.photo_link) LIKE '%.webp%' THEN 'legacy-proof.webp'
      WHEN lower(mr.photo_link) LIKE '%.gif%'  THEN 'legacy-proof.gif'
      ELSE 'legacy-proof.jpg'
    END,
    'content_type', CASE
      WHEN lower(mr.photo_link) LIKE '%.pdf%' THEN 'application/pdf'
      ELSE 'image/jpeg'
    END,
    'size_bytes', 0,
    'uploaded_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
  )
)
WHERE mr.photo_link IS NOT NULL
  AND btrim(mr.photo_link) <> ''
  AND mr.attachments = '[]'::jsonb;

-- Step 1: drop legacy attachment tables
DROP TABLE IF EXISTS homework_files CASCADE;
DROP TABLE IF EXISTS mock_files CASCADE;
