-- 001_order_lines.sql
-- ADDITIVE ONLY: creates app_users (approved-user allowlist), order_lines and the
-- atomic receive function. Does not modify led_inventory, santaf_inventory,
-- santaf_movements or leds_inventory, and does not change anyone's access to them.
-- Run manually in the SQL Editor of project "pergola-system" ONLY.
-- Everything runs in one transaction: if any step fails, nothing is applied.
-- Production audit (PostgreSQL 17.6) was checked against this file.

BEGIN;

-- 0. Pre-checks: abort if the production schema is not what the app expects ----
DO $$
DECLARE missing text;
BEGIN
  SELECT string_agg(t || '.' || c, ', ') INTO missing
  FROM (VALUES
    ('led_inventory','order_number'), ('led_inventory','led_type'),
    ('led_inventory','quantity'),     ('led_inventory','notes'),
    ('santaf_inventory','id'),        ('santaf_inventory','length'),
    ('santaf_inventory','quantity'),
    ('santaf_movements','length'),    ('santaf_movements','quantity'),
    ('santaf_movements','type'),      ('santaf_movements','date')
  ) AS need(t, c)
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = need.t AND column_name = need.c);
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'ABORT: expected columns missing (wrong project or schema changed): %', missing;
  END IF;

  IF to_regclass('auth.users') IS NULL OR to_regprocedure('auth.uid()') IS NULL THEN
    RAISE EXCEPTION 'ABORT: Supabase auth schema not found (auth.users / auth.uid())';
  END IF;

  IF to_regclass('public.order_lines') IS NOT NULL AND (
       SELECT count(*) FROM information_schema.columns
       WHERE table_schema='public' AND table_name='order_lines'
         AND column_name IN ('customer_order_number','category','length_mm','quantity',
                             'status','stock_applied','storage_location')) <> 7 THEN
    RAISE EXCEPTION 'ABORT: a different public.order_lines table already exists';
  END IF;
  IF to_regclass('public.app_users') IS NOT NULL AND (
       SELECT count(*) FROM information_schema.columns
       WHERE table_schema='public' AND table_name='app_users'
         AND column_name IN ('user_id','email','role','active')) <> 4 THEN
    RAISE EXCEPTION 'ABORT: a different public.app_users table already exists';
  END IF;
END $$;

-- 1. Approved-user allowlist ----------------------------------------------------
-- A logged-in account has NO access to inventory data unless it is listed here
-- and active. Only the project owner (SQL Editor) can add or change rows: the
-- table is not reachable through the API by anon or authenticated.
CREATE TABLE IF NOT EXISTS public.app_users (
  user_id    uuid        PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  email      text        NOT NULL,
  role       text        NOT NULL DEFAULT 'warehouse' CHECK (role IN ('admin', 'warehouse')),
  active     boolean     NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.app_users ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.app_users FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.is_approved_user() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$ SELECT EXISTS (SELECT 1 FROM public.app_users WHERE user_id = auth.uid() AND active) $$;

REVOKE ALL ON FUNCTION public.is_approved_user() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.is_approved_user() TO authenticated;

-- 2. Order lines ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.order_lines (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_order_number text        NOT NULL CHECK (length(btrim(customer_order_number)) > 0),
  category              text        NOT NULL CHECK (category IN ('LED', 'SANTAF')),
  supplier_order_ref    text,
  supplier_order_date   date,
  ordered_by            text,
  item_spec             text,
  length_mm             integer     NOT NULL CHECK (length_mm > 0),
  quantity              integer     NOT NULL CHECK (quantity > 0),
  status                text        NOT NULL DEFAULT 'ordered'
                                    CHECK (status IN ('ordered', 'received', 'cancelled')),
  stock_applied         boolean     NOT NULL DEFAULT false,
  coordination_date     date,
  received_date         date,
  received_qty          integer     CHECK (received_qty IS NULL OR received_qty > 0),
  storage_location      text,
  notes                 text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT order_lines_stock_requires_receipt
    CHECK (stock_applied = false OR (status = 'received' AND received_qty IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS order_lines_order_idx  ON public.order_lines (customer_order_number);
CREATE INDEX IF NOT EXISTS order_lines_coord_idx  ON public.order_lines (coordination_date);
CREATE INDEX IF NOT EXISTS order_lines_status_idx ON public.order_lines (status);

-- Access: only approved, logged-in users. NO anonymous access, NO delete for anyone.
ALTER TABLE public.order_lines ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT policyname FROM pg_policies
           WHERE schemaname = 'public' AND tablename = 'order_lines'
  LOOP
    EXECUTE format('DROP POLICY %I ON public.order_lines', r.policyname);
  END LOOP;
END $$;
CREATE POLICY order_lines_select ON public.order_lines FOR SELECT TO authenticated
  USING (public.is_approved_user());
CREATE POLICY order_lines_insert ON public.order_lines FOR INSERT TO authenticated
  WITH CHECK (public.is_approved_user());
CREATE POLICY order_lines_update ON public.order_lines FOR UPDATE TO authenticated
  USING (public.is_approved_user()) WITH CHECK (public.is_approved_user());

REVOKE ALL ON public.order_lines FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.order_lines TO authenticated;

-- 3. Atomic warehouse receipt ----------------------------------------------------
-- One transaction: lock the line, check it is still 'ordered', add the stock,
-- write the movement, mark the line received. A second call (double click, two
-- users, retry) finds status <> 'ordered' and fails WITHOUT touching stock.
-- SECURITY DEFINER (bypasses RLS), so it checks approval itself; EXECUTE is
-- granted only to logged-in users and revoked from anon.
CREATE OR REPLACE FUNCTION public.receive_order_line(
  p_line_id  uuid,
  p_qty      integer,
  p_date     date,
  p_location text
) RETURNS public.order_lines
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $fn$
DECLARE
  l     public.order_lines;
  moved integer;
  d     date := coalesce(p_date, current_date);
BEGIN
  IF NOT public.is_approved_user() THEN
    RAISE EXCEPTION 'ORDERERR:אין הרשאה לבצע פעולה זו. פנו למנהל המערכת.';
  END IF;
  IF p_qty IS NULL OR p_qty <= 0 THEN
    RAISE EXCEPTION 'ORDERERR:הכמות שהתקבלה חייבת להיות גדולה מאפס.';
  END IF;
  IF btrim(coalesce(p_location, '')) = '' THEN
    RAISE EXCEPTION 'ORDERERR:חובה להזין מיקום אחסון.';
  END IF;

  SELECT * INTO l FROM public.order_lines WHERE id = p_line_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ORDERERR:שורת ההזמנה לא נמצאה.';
  END IF;
  IF l.status <> 'ordered' OR l.stock_applied THEN
    RAISE EXCEPTION 'ORDERERR:השורה כבר התקבלה או בוטלה. לא בוצע שינוי במלאי.';
  END IF;

  IF l.category = 'SANTAF' THEN
    UPDATE public.santaf_inventory
       SET quantity = coalesce(quantity, 0) + p_qty
     WHERE length = l.length_mm;
    GET DIAGNOSTICS moved = ROW_COUNT;
    IF moved <> 1 THEN
      RAISE EXCEPTION 'ORDERERR:לא נמצאה שורת מלאי יחידה לאורך סנטף %. לא בוצע שינוי.', l.length_mm;
    END IF;
    INSERT INTO public.santaf_movements (length, quantity, type, "date")
    VALUES (l.length_mm, p_qty, 'IN', d);
  ELSE
    INSERT INTO public.led_inventory (order_number, led_type, quantity, notes)
    VALUES (
      coalesce(nullif(btrim(l.supplier_order_ref), ''), l.customer_order_number),
      btrim(concat(l.item_spec, ' ', trim_scale((l.length_mm / 1000.0)::numeric)::text, ' מ''')),
      p_qty,
      'הזמנת פרגולה ' || l.customer_order_number || ' | מיקום: ' || btrim(p_location)
    );
  END IF;

  UPDATE public.order_lines
     SET status = 'received', stock_applied = true, received_qty = p_qty,
         received_date = d, storage_location = btrim(p_location), updated_at = now()
   WHERE id = p_line_id
   RETURNING * INTO l;
  RETURN l;
END
$fn$;

REVOKE ALL ON FUNCTION public.receive_order_line(uuid, integer, date, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.receive_order_line(uuid, integer, date, text) TO authenticated;

COMMIT;
