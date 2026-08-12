-- Store Cloudinary metadata for the class-wide mock test paper.
-- Distinct from assignments.homework_document and mock_results.attachments.
ALTER TABLE sessions
ADD COLUMN IF NOT EXISTS mock_document JSONB;
