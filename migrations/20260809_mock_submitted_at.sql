-- Add explicit submission timestamp for mock results.
-- Backfill from latest uploaded attachment timestamp for already submitted rows.

ALTER TABLE mock_results
  ADD COLUMN IF NOT EXISTS submitted_at timestamptz;

UPDATE mock_results AS mr
SET submitted_at = latest.latest_uploaded_at
FROM (
  SELECT
    r.id,
    MAX(NULLIF(att.item->>'uploaded_at', '')::timestamptz) AS latest_uploaded_at
  FROM mock_results AS r
  LEFT JOIN LATERAL jsonb_array_elements(COALESCE(r.attachments, '[]'::jsonb)) AS att(item) ON TRUE
  WHERE r.submitted = TRUE
  GROUP BY r.id
) AS latest
WHERE mr.id = latest.id
  AND mr.submitted = TRUE
  AND mr.submitted_at IS NULL
  AND latest.latest_uploaded_at IS NOT NULL;
