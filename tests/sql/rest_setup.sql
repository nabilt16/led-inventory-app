-- Local integration setup (throwaway Postgres + PostgREST). Fake data only.
-- Mimics Supabase roles and the ASSUMED legacy schema; real prod schema not verified.
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE authenticator LOGIN NOINHERIT PASSWORD 'localtest';
GRANT anon, authenticated TO authenticator;
GRANT USAGE ON SCHEMA public TO anon, authenticated;

CREATE TABLE led_inventory (id bigserial PRIMARY KEY, order_number text, led_type text,
  quantity integer, notes text, created_at timestamptz DEFAULT now());
CREATE TABLE santaf_inventory (id bigserial PRIMARY KEY, length integer, quantity integer,
  min_quantity integer);
CREATE TABLE santaf_movements (id bigserial PRIMARY KEY, length integer, quantity integer,
  type text, date date);

INSERT INTO santaf_inventory (length, quantity, min_quantity)
  SELECT l, 0, 20 FROM unnest(ARRAY[1500,2000,2500,3000,3500,4000,4500,5000,5500,6000,6500,7000,7500,8000]) l;
-- an extra length used to test "no inventory row" failure is simply NOT inserted (e.g. 9999)

-- Simulate today's presumed state: RLS on, anon may do everything (what we want to remove)
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['led_inventory','santaf_inventory','santaf_movements'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY anon_all ON %I FOR ALL TO anon USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %I TO anon', t);
  END LOOP;
END $$;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon;

-- Test hook: a movement of quantity 777 fails AFTER the stock was already updated,
-- to prove the whole receipt rolls back atomically.
CREATE FUNCTION test_fail_777() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NEW.quantity = 777 THEN RAISE EXCEPTION 'simulated failure'; END IF; RETURN NEW; END $$;
CREATE TRIGGER test_fail_777 BEFORE INSERT ON santaf_movements
  FOR EACH ROW EXECUTE FUNCTION test_fail_777();
