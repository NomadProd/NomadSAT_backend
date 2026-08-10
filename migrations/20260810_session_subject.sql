-- Add optional subject on sessions (required for session_type = 'review').
-- Reuses the same verbal/math subject labels used elsewhere in the app.

ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS subject VARCHAR NULL;
