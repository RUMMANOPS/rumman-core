-- Task availability windows: when a task opens/becomes available vs when it closes/is due.
-- opens_at: when the task becomes available to work on (optional, defaults to due_at in UI)
-- closes_at: when the task closes (optional, may equal due_at)
ALTER TABLE student_tasks
  ADD COLUMN IF NOT EXISTS opens_at  timestamptz NULL,
  ADD COLUMN IF NOT EXISTS closes_at timestamptz NULL;
