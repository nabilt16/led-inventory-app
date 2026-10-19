-- audit_readonly.sql
-- READ-ONLY metadata audit for project "pergola-system". Returns structure only:
-- no row data, no secrets. Run each block in the Supabase SQL Editor and send the
-- results back. It changes nothing.

-- 1. Columns and types of the three existing tables (and order_lines if present)
SELECT table_name, ordinal_position AS pos, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('led_inventory','santaf_inventory','santaf_movements','order_lines')
ORDER BY table_name, ordinal_position;

-- 2. Constraints (PK / UNIQUE / CHECK / FK)
SELECT conrelid::regclass AS tbl, conname, contype, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE connamespace = 'public'::regnamespace
  AND conrelid::regclass::text IN ('led_inventory','santaf_inventory','santaf_movements','order_lines')
ORDER BY 1, 2;

-- 3. Triggers on those tables
SELECT event_object_table AS tbl, trigger_name, event_manipulation, action_timing, action_statement
FROM information_schema.triggers
WHERE trigger_schema = 'public'
ORDER BY 1, 2;

-- 4. RLS state and policies (this is the key block for the "public writes" question)
SELECT c.relname AS tbl, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced
FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' ORDER BY 1;

SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname;

-- 5. Table privileges granted to anon / authenticated
SELECT table_name, grantee, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon','authenticated','public')
GROUP BY table_name, grantee ORDER BY 1, 2;

-- 6. Row counts and aggregate quantities only (no row contents)
SELECT 'led_inventory' AS tbl, count(*) AS row_count, coalesce(sum(quantity),0) AS total_qty FROM led_inventory
UNION ALL
SELECT 'santaf_inventory', count(*), coalesce(sum(quantity),0) FROM santaf_inventory
UNION ALL
SELECT 'santaf_movements', count(*), coalesce(sum(quantity),0) FROM santaf_movements;

-- 7. Duplicate santaf lengths (receive requires exactly one row per length)
SELECT length, count(*) AS rows_for_length
FROM santaf_inventory GROUP BY length HAVING count(*) <> 1;

-- 8. Auth setup: are there users, and are sign-ups open? (counts only)
SELECT count(*) AS auth_users FROM auth.users;
-- Sign-up setting is NOT visible in SQL: check Dashboard > Authentication > Sign In / Providers
-- > "Allow new users to sign up" (must be OFF) and Email provider enabled.
