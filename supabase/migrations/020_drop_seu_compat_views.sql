-- Migration 020: Drop backward-compatibility seu_* views
--
-- Created in migration 014 when seu_* tables were renamed to inst_*.
-- All services have been confirmed to use inst_* directly.
-- Codebase scan (2026-05-30) shows zero references to seu_colleges,
-- seu_specializations, or seu_courses in deployed app code.

DROP VIEW IF EXISTS seu_colleges;
DROP VIEW IF EXISTS seu_specializations;
DROP VIEW IF EXISTS seu_courses;
