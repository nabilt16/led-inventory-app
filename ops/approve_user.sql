-- approve_user.sql
-- Approves (or re-activates) an EXISTING Supabase Auth user for inventory access.
-- Step 1 (dashboard): Authentication > Users > Add user (email + password you choose).
-- Step 2 (here): replace the email below with that user's email and run.
-- This is also the recovery tool if someone is locked out: it never opens access
-- to anonymous users. Does not touch inventory data. Never put a password here.

INSERT INTO public.app_users (user_id, email, role, active)
SELECT id, email, 'admin', true
FROM auth.users
WHERE lower(email) = lower('PUT-THE-USER-EMAIL-HERE@example.com')
ON CONFLICT (user_id) DO UPDATE SET active = true, role = EXCLUDED.role;

-- Verify: must return one row with active = true
SELECT email, role, active FROM public.app_users ORDER BY created_at;
