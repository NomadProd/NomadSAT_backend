-- Store Cloudinary metadata for the assignment homework PDF.
-- The PDF itself lives in Cloudinary; task_link is never removed.
ALTER TABLE assignments
ADD COLUMN IF NOT EXISTS homework_document JSONB;
