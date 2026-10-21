-- rollback_001.sql
-- Removes everything 001 added (tables, functions, index, CHECKs) and restores the original
-- structure. Safe-guarded: refuses to run if order_lines contains rows (real orders).
-- Does not touch legacy inventory rows.
-- ORDER: if 002 was applied, its policies depend on is_approved_user(), so this fails until
-- 002 is reverted (ops/emergency_revert_002.sql). Prefer fixing forward over rolling back.
-- NOTE: after rolling back, the live app version that uses these functions must not be running.
BEGIN;
DO $$
BEGIN
  IF to_regclass('public.order_lines') IS NOT NULL
     AND (SELECT count(*) FROM public.order_lines) > 0 THEN
    RAISE EXCEPTION 'ABORT: order_lines contains rows; not dropping real order data';
  END IF;
END $$;
DROP FUNCTION IF EXISTS public.pick_stock_line(uuid, integer, date, text);
DROP FUNCTION IF EXISTS public.unpick_stock_line(uuid, date);
DROP FUNCTION IF EXISTS public.issue_order_lines(uuid[], date, text);
DROP FUNCTION IF EXISTS public.santaf_receive_stock(integer[], integer[], date);
DROP FUNCTION IF EXISTS public.santaf_issue_direct(text, integer, integer, date);
DROP FUNCTION IF EXISTS public.led_issue_stock(integer, integer);
DROP FUNCTION IF EXISTS public.guard_duplicate(text);
DROP TABLE IF EXISTS public.order_lines;
DROP TABLE IF EXISTS public.recent_actions;
DROP FUNCTION IF EXISTS public.order_lines_guard();
DROP FUNCTION IF EXISTS public.is_approved_user();
DROP TABLE IF EXISTS public.app_users;
DROP INDEX IF EXISTS public.santaf_inventory_length_uq;
ALTER TABLE public.led_inventory DROP CONSTRAINT IF EXISTS led_inventory_qty_nonneg;
ALTER TABLE public.santaf_inventory DROP CONSTRAINT IF EXISTS santaf_inventory_qty_nonneg;
COMMIT;
