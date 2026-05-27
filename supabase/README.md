# Supabase Migrations

Schema changes for RUMMAN's Supabase database.

## How to apply a migration

1. Open the Supabase dashboard → SQL Editor → New query
2. Paste the contents of the migration file
3. Run it
4. Verify the tables exist before running the affected worker

## Migrations

| File | Tables created | Required by |
|---|---|---|
| `001_daily_brief_tables.sql` | `brief_runs`, `extracted_items` | `app/daily_brief.py` |

## Why not Supabase CLI

The service-role key is not in the repository `.env` (only the anon key is). Until the service-role key is available in the local environment, migrations are applied manually via the SQL editor.

Once the service-role key is added to `.env`, switch to:
```bash
supabase db push
```

## Schema-as-code rule

Every schema change must go through a migration file here before being applied. Do not create or alter tables directly in the Supabase UI without a corresponding file in this directory.

This rule exists because schema changes applied only through the UI are invisible to git, unreviable, and unreproducible. See ADR-0003.
