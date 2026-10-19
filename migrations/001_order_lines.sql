-- 001_order_lines.sql
-- ADDITIVE ONLY: creates the order_lines table + an atomic receive function.
-- Does not modify led_inventory, santaf_inventory or santaf_movements (it only
-- INSERTs/UPDATEs them later, when a user confirms a physical receipt).
-- Run manually in the SQL Editor of project "pergola-system" ONLY, after
-- running ops/audit_readonly.sql and reviewing its output.
-- Everything runs in one transaction: if any step fails, nothing is applied.

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

  -- if an order_lines table already exists it must be ours, otherwise abort
  IF to_regclass('public.order_lines') IS NOT NULL AND (
       SELECT count(*) FROM information_schema.columns
       WHERE table_schema='public' AND table_name='order_lines'
         AND column_name IN ('customer_order_number','category','length_mm','quantity',
                             'status','stock_applied','storage_location')) <> 7 THEN
    RAISE EXCEPTION 'ABORT: a different public.order_lines table already exists';
  END IF;
END $$;

-- 1. Table ---------------------------------------------------------------------
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

-- 2. Access: logged-in users only (role "authenticated"). NO anonymous access,
--    NO delete for anyone. -----------------------------------------------------
ALTER TABLE public.order_lines ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS order_lines_anon_select ON public.order_lines;
DROP POLICY IF EXISTS order_lines_anon_insert ON public.order_lines;
DROP POLICY IF EXISTS order_lines_anon_update ON public.order_lines;
DROP POLICY IF EXISTS order_lines_auth_select ON public.order_lines;
DROP POLICY IF EXISTS order_lines_auth_insert ON public.order_lines;
DROP POLICY IF EXISTS order_lines_auth_update ON public.order_lines;
CREATE POLICY order_lines_auth_select ON public.order_lines FOR SELECT TO authenticated USING (true);
CREATE POLICY order_lines_auth_insert ON public.order_lines FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY order_lines_auth_update ON public.order_lines FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

REVOKE ALL ON public.order_lines FROM PUBLIC, anon;
GRANT SELECT, INSERT, UPDATE ON public.order_lines TO authenticated;

-- 3. Atomic warehouse receipt ----------------------------------------------------
-- One transaction: lock the line, check it is still 'ordered', add the stock,
-- write the movement, mark the line received. A second call (double click, two
-- users, retry) finds status <> 'ordered' and fails WITHOUT touching stock.
-- SECURITY DEFINER so it works regardless of the current policies on the legacy
-- tables (before or after 002). Access control is the EXECUTE grant below:
-- only logged-in users ("authenticated") may call it; anon cannot.
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
