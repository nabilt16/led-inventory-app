-- rollback_001.sql
-- Removes what 001 added. Safe-guarded: refuses to run if order_lines contains rows
-- (to avoid destroying real orders). Does not touch legacy inventory tables.
-- ORDER: if 002 was applied, its policies depend on is_approved_user(), so this fails until
-- 002 is reverted (ops/emergency_revert_002.sql). Prefer fixing forward over rolling back.
BEGIN;
DO $$
BEGIN
  IF to_regclass('public.order_lines') IS NOT NULL
     AND (SELECT count(*) FROM public.order_lines) > 0 THEN
    RAISE EXCEPTION 'ABORT: order_lines contains rows; not dropping real order data';
  END IF;
END $$;
DROP FUNCTION IF EXISTS public.receive_order_line(uuid, integer, date, text);
DROP TABLE IF EXISTS public.order_lines;
DROP FUNCTION IF EXISTS public.is_approved_user();
DROP TABLE IF EXISTS public.app_users;
COMMIT;
