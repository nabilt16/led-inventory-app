-- Local integration setup (throwaway Postgres + PostgREST). FAKE data only.
-- Mirrors the production state found by the read-only audit (PostgreSQL 17.6):
--   * 4 legacy tables, RLS OFF, no policies, anon AND authenticated hold ALL privileges
--   * santaf_movements."date" is timestamp; led_inventory.created_at is timestamp
--   * extra unused table leds_inventory
--   * 18 led rows (total 50), 14 santaf rows (total 179), 31 movements, 0 auth users
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE authenticator LOGIN NOINHERIT PASSWORD 'localtest';
GRANT anon, authenticated TO authenticator;
GRANT USAGE ON SCHEMA public TO anon, authenticated;

-- Minimal stand-in for Supabase's auth schema
CREATE SCHEMA auth;
CREATE TABLE auth.users (id uuid PRIMARY KEY, email text);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                  (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'))::uuid $$;
GRANT USAGE ON SCHEMA auth TO anon, authenticated;
GRANT EXECUTE ON FUNCTION auth.uid() TO anon, authenticated;

CREATE TABLE led_inventory (id serial PRIMARY KEY, order_number text, led_type text,
  quantity integer, created_at timestamp DEFAULT now(), notes text);
CREATE TABLE leds_inventory (id serial PRIMARY KEY, order_number text, led_type text,
  quantity integer, created_at timestamp DEFAULT now());
CREATE TABLE santaf_inventory (id serial PRIMARY KEY, length integer, quantity integer DEFAULT 0,
  min_quantity integer DEFAULT 20, updated_at timestamp DEFAULT now());
CREATE TABLE santaf_movements (id serial PRIMARY KEY, length integer, quantity integer,
  type text, date timestamp DEFAULT now());

INSERT INTO led_inventory (order_number, led_type, quantity, notes)
  SELECT 'ORD-' || g, 'LED type ' || g, CASE WHEN g <= 14 THEN 3 ELSE 2 END, 'note ' || g
  FROM generate_series(1, 18) g;                                    -- total 50
INSERT INTO leds_inventory (order_number, led_type, quantity) VALUES ('OLD-1', 'legacy', 4), ('OLD-2', 'legacy', 1);
INSERT INTO santaf_inventory (length, quantity, min_quantity)
  SELECT l, CASE WHEN l = 1500 THEN 10 ELSE 13 END, 20
  FROM unnest(ARRAY[1500,2000,2500,3000,3500,4000,4500,5000,5500,6000,6500,7000,7500,8000]) l;  -- total 179
INSERT INTO santaf_movements (length, quantity, type, date)
  SELECT 1500 + 500 * (g % 14), 1 + g % 5, CASE WHEN g % 3 = 0 THEN 'OUT' ELSE 'IN' END,
         timestamp '2026-09-01' + g * interval '1 day'
  FROM generate_series(1, 31) g;

-- Today's production state: RLS off, everything granted to both roles
GRANT ALL ON led_inventory, leds_inventory, santaf_inventory, santaf_movements TO anon, authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;

-- Test-only hook: a movement of quantity 777 fails AFTER the stock was updated,
-- to prove the whole receipt rolls back atomically.
CREATE FUNCTION test_fail_777() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NEW.quantity = 777 THEN RAISE EXCEPTION 'simulated failure'; END IF; RETURN NEW; END $$;
CREATE TRIGGER test_fail_777 BEFORE INSERT ON santaf_movements
  FOR EACH ROW EXECUTE FUNCTION test_fail_777();

-- Test users: approved admin, approved-then-deactivated, and a logged-in stranger
INSERT INTO auth.users VALUES
  ('11111111-1111-1111-1111-111111111111', 'admin@example.test'),
  ('22222222-2222-2222-2222-222222222222', 'stranger@example.test'),
  ('33333333-3333-3333-3333-333333333333', 'former@example.test');
