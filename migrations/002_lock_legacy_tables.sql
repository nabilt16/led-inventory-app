-- 002_lock_legacy_tables.sql
-- STAGE B HARDENING. Run ONLY after:
--   (1) the login-enabled app version is deployed and a login was verified, and
--   (2) 001_order_lines.sql has been applied.
-- Effect: led_inventory, santaf_inventory and santaf_movements stop accepting
-- anonymous access. Only logged-in users (role "authenticated") can read, insert
-- and update; nobody can delete through the API. Rows and data are not touched.
-- Running it BEFORE the login version is live will make the OLD app stop working.

BEGIN;

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT schemaname, tablename, policyname FROM pg_policies
           WHERE schemaname = 'public'
             AND tablename IN ('led_inventory', 'santaf_inventory', 'santaf_movements')
  LOOP
    EXECUTE format('DROP POLICY %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
  END LOOP;
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['led_inventory', 'santaf_inventory', 'santaf_movements'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, anon', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO authenticated', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING (true)',
                   t || '_auth_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT TO authenticated WITH CHECK (true)',
                   t || '_auth_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE TO authenticated USING (true) WITH CHECK (true)',
                   t || '_auth_update', t);
  END LOOP;
END $$;

-- identity/serial columns used by inserts
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;

COMMIT;
