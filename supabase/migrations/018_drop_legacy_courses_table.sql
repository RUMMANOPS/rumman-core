-- Migration 018: Drop the legacy `courses` table (72 rows, migration 003 era)
--
-- inst_courses (157 rows) is the authoritative table since migration 008/014.
-- No app code references rest/v1/courses — verified by codebase search.
-- The seu_courses backward-compat VIEW is unaffected (it points at inst_courses).

DROP TABLE IF EXISTS courses CASCADE;
