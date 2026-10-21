-- 002_lock_legacy_tables.sql
-- Closes anonymous access to the existing tables AND removes the right to change stock
-- columns directly. Run ONLY after:
--   (1) 001_order_lines.sql was applied,
--   (2) the owner is approved (ops/approve_user.sql), and
--   (3) the login-enabled app is deployed and a real login was verified.
-- Production audit state before this script: RLS OFF on led_inventory, leds_inventory,
-- santaf_inventory, santaf_movements; anon AND authenticated hold ALL privileges
-- (incl. DELETE/TRUNCATE); no policies. Row data is NOT touched.
--
-- After it (anon: nothing; authenticated only if approved in app_users):
--   led_inventory     SELECT, INSERT         (quantity changes only via led_issue_stock)
--   santaf_inventory  SELECT, INSERT, UPDATE of min_quantity ONLY
--                     (quantity changes only via the atomic functions of 001)
--   santaf_movements  SELECT only            (history is written only by those functions)
--   leds_inventory    SELECT only            (unused legacy table, preserved)
-- Nobody can DELETE or TRUNCATE through the API.
-- Idempotent: safe to run again. One transaction: any failure applies nothing.

BEGIN;

DO $$
BEGIN
  IF to_regprocedure('public.is_approved_user()') IS NULL
     OR to_regprocedure('public.santaf_receive_stock(integer[],integer[],date)') IS NULL
     OR to_regprocedure('public.santaf_issue_direct(text,integer,integer,date)') IS NULL
     OR to_regprocedure('public.led_issue_stock(integer,integer)') IS NULL THEN
    RAISE EXCEPTION 'ABORT: run 001_order_lines.sql first (stock functions missing)';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.app_users WHERE active) THEN
    RAISE EXCEPTION 'ABORT: no approved active user exists; running this would lock everyone out (use ops/approve_user.sql)';
  END IF;
END $$;

DO $$
DECLARE r record; t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['led_inventory', 'santaf_inventory', 'santaf_movements', 'leds_inventory'] LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    FOR r IN SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = t LOOP
      EXECUTE format('DROP POLICY %I ON public.%I', r.policyname, t);
    END LOOP;
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, anon, authenticated', t);
    EXECUTE format('GRANT SELECT ON public.%I TO authenticated', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING (public.is_approved_user())',
                   t || '_select', t);
  END LOOP;
END $$;

-- led_inventory: new rows only (issuing goes through led_issue_stock)
GRANT INSERT ON public.led_inventory TO authenticated;
CREATE POLICY led_inventory_insert ON public.led_inventory FOR INSERT TO authenticated
  WITH CHECK (public.is_approved_user());

-- santaf_inventory: new length rows (start-up) and the minimum threshold; never quantity
GRANT INSERT ON public.santaf_inventory TO authenticated;
GRANT UPDATE (min_quantity) ON public.santaf_inventory TO authenticated;
CREATE POLICY santaf_inventory_insert ON public.santaf_inventory FOR INSERT TO authenticated
  WITH CHECK (public.is_approved_user());
CREATE POLICY santaf_inventory_update ON public.santaf_inventory FOR UPDATE TO authenticated
  USING (public.is_approved_user()) WITH CHECK (public.is_approved_user());

-- serial columns: inserts by the owner need the sequences; anon must not
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, anon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;

COMMIT;
