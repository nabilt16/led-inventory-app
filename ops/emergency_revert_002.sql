-- emergency_revert_002.sql
-- LAST RESORT. Restores the ORIGINAL production state recorded by the audit:
-- RLS OFF and ALL privileges for anon and authenticated on the four legacy tables.
-- That means PUBLIC WRITES ARE RE-ENABLED. Use only if the login-enabled app is
-- unusable AND ops/approve_user.sql cannot fix it, and re-run 002 as soon as possible.
-- Does not touch row data, order_lines or app_users.

BEGIN;
DO $$
DECLARE r record; t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['led_inventory', 'santaf_inventory', 'santaf_movements', 'leds_inventory'] LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    FOR r IN SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = t LOOP
      EXECUTE format('DROP POLICY %I ON public.%I', r.policyname, t);
    END LOOP;
    EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', t);
    EXECUTE format('GRANT ALL ON public.%I TO anon, authenticated', t);
  END LOOP;
END $$;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
COMMIT;
