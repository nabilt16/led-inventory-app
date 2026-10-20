-- 002_lock_legacy_tables.sql
-- Closes anonymous access to the existing tables. Run ONLY after:
--   (1) 001_order_lines.sql was applied,
--   (2) at least one user is approved (ops/approve_user.sql), and
--   (3) the login-enabled app is deployed and a real login was verified.
-- Production audit state before this script: RLS OFF on led_inventory,
-- leds_inventory, santaf_inventory, santaf_movements; anon AND authenticated hold
-- ALL privileges (incl. DELETE/TRUNCATE); no policies. Row data is NOT touched.
--
-- After it: anon has no access; authenticated has access only if approved
-- (app_users); no DELETE/TRUNCATE for anyone through the API; leds_inventory
-- (not used by the app) becomes read-only for approved users.
-- Idempotent: safe to run again. One transaction: any failure applies nothing.

BEGIN;

DO $$
BEGIN
  IF to_regprocedure('public.is_approved_user()') IS NULL THEN
    RAISE EXCEPTION 'ABORT: run 001_order_lines.sql first';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.app_users WHERE active) THEN
    RAISE EXCEPTION 'ABORT: no approved active user exists; running this would lock everyone out (use ops/approve_user.sql)';
  END IF;
END $$;

DO $$
DECLARE r record; t text; rw boolean;
BEGIN
  FOREACH t IN ARRAY ARRAY['led_inventory', 'santaf_inventory', 'santaf_movements', 'leds_inventory'] LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    rw := t <> 'leds_inventory';

    FOR r IN SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = t LOOP
      EXECUTE format('DROP POLICY %I ON public.%I', r.policyname, t);
    END LOOP;

    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, anon, authenticated', t);
    EXECUTE format('GRANT SELECT ON public.%I TO authenticated', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING (public.is_approved_user())',
                   t || '_select', t);
    IF rw THEN
      EXECUTE format('GRANT INSERT, UPDATE ON public.%I TO authenticated', t);
      EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT TO authenticated WITH CHECK (public.is_approved_user())',
                     t || '_insert', t);
      EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE TO authenticated USING (public.is_approved_user()) WITH CHECK (public.is_approved_user())',
                     t || '_update', t);
    END IF;
  END LOOP;
END $$;

-- serial columns: inserts by logged-in users need the sequences; anon must not
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, anon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;

COMMIT;
