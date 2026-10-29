-- The ask box is for signed-in users with a daily quota; the Firebase uid is kept for the count.
ALTER TABLE ask_answers ADD COLUMN user_id text;
CREATE INDEX ON ask_answers (user_id, created_at);
