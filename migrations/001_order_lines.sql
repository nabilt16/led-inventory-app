-- 001_order_lines.sql
-- ADDITIVE ONLY: owner approval table, order_lines, three atomic stock functions.
-- Does not modify led_inventory, santaf_inventory, santaf_movements or leds_inventory
-- and does not change anyone's access to them (that is 002).
-- Run manually in the SQL Editor of project "pergola-system" ONLY.
-- One transaction: if any step fails, nothing is applied.
--
-- Design (single owner):
--  * order_lines holds ORDER information. Registering a line or changing its supplier
--    status NEVER changes stock.
--  * Standard Santaf stock (santaf_inventory, 14 fixed lengths) changes ONLY through
--    pick_stock_line / unpick_stock_line (available stock -> staged) and, at issuance,
--    a movement is logged without deducting again.
--  * Customer-specific Santaf and supplier-prepared LED packages never touch general stock.

BEGIN;

-- 0. Pre-checks ---------------------------------------------------------------
DO $$
DECLARE missing text;
BEGIN
  SELECT string_agg(t || '.' || c, ', ') INTO missing
  FROM (VALUES
    ('santaf_inventory','id'), ('santaf_inventory','length'), ('santaf_inventory','quantity'),
    ('santaf_movements','length'), ('santaf_movements','quantity'),
    ('santaf_movements','type'), ('santaf_movements','date')
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
         AND column_name IN ('customer_order_number','category','supply','package_no',
                             'supplier_status','picked_qty','issued_on')) <> 7 THEN
    RAISE EXCEPTION 'ABORT: a different public.order_lines table already exists';
  END IF;
  IF to_regclass('public.app_users') IS NOT NULL AND (
       SELECT count(*) FROM information_schema.columns
       WHERE table_schema='public' AND table_name='app_users'
         AND column_name IN ('user_id','email','active')) <> 3 THEN
    RAISE EXCEPTION 'ABORT: a different public.app_users table already exists';
  END IF;
END $$;

-- 1. Owner approval --------------------------------------------------------------
-- The database only serves accounts listed (and active) here. One row = the owner.
-- Not reachable through the API; managed from the SQL Editor (ops/approve_user.sql).
CREATE TABLE IF NOT EXISTS public.app_users (
  user_id    uuid        PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  email      text        NOT NULL,
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

-- 2. Order lines -------------------------------------------------------------------
-- supplier_status: NULL = לא הוזמן, 'ordered' = הוזמן, 'pending' = בהמתנה, 'received' = התקבל
-- supply: 'supplier' = ordered specifically for the customer order (always for LED,
--         and for customer-specific / colored / long Santaf)
--         'stock'    = standard Santaf taken from regular warehouse stock (14 fixed lengths)
-- package_no: LED supplier packages. Lines of one order with the same package_no are one
--         sealed package and share status/receipt/location (the app updates them together).
CREATE TABLE IF NOT EXISTS public.order_lines (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_order_number text        NOT NULL CHECK (length(btrim(customer_order_number)) > 0),
  category              text        NOT NULL CHECK (category IN ('LED', 'SANTAF')),
  supply                text        NOT NULL DEFAULT 'supplier' CHECK (supply IN ('supplier', 'stock')),
  package_no            integer     NOT NULL DEFAULT 1 CHECK (package_no > 0),
  item_spec             text,
  color                 text,
  length_mm             integer     NOT NULL CHECK (length_mm > 0),
  quantity              integer     NOT NULL CHECK (quantity > 0),
  supplier_ref          text,
  supplier_order_date   date,
  supplier_status       text        CHECK (supplier_status IN ('ordered', 'pending', 'received')),
  coordination_date     date,
  received_date         date,
  storage_location      text,
  staging_location      text,
  picked_qty            integer     NOT NULL DEFAULT 0 CHECK (picked_qty >= 0),
  picked_on             date,
  issued_on             date,
  recipient             text,
  notes                 text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ol_led_is_supplier  CHECK (category = 'SANTAF' OR supply = 'supplier'),
  CONSTRAINT ol_stock_not_ordered CHECK (supply = 'supplier' OR supplier_status IS NULL),
  CONSTRAINT ol_stock_standard   CHECK (supply <> 'stock' OR color IS NULL OR color = 'שקוף'),
  CONSTRAINT ol_pick_only_stock  CHECK (supply = 'stock' OR picked_qty = 0),
  CONSTRAINT ol_pick_le_qty      CHECK (picked_qty <= quantity),
  CONSTRAINT ol_received_needs_date CHECK (supplier_status IS DISTINCT FROM 'received' OR received_date IS NOT NULL),
  CONSTRAINT ol_date_only_if_received CHECK (received_date IS NULL OR supplier_status IS NOT DISTINCT FROM 'received'),
  CONSTRAINT ol_issue_needs_recipient CHECK (issued_on IS NULL OR length(btrim(coalesce(recipient, ''))) > 0),
  CONSTRAINT ol_issue_requires_ready CHECK (
    issued_on IS NULL
    OR (supply = 'supplier' AND supplier_status IS NOT DISTINCT FROM 'received')
    OR (supply = 'stock' AND picked_qty = quantity))
);

CREATE INDEX IF NOT EXISTS order_lines_order_idx  ON public.order_lines (customer_order_number);
CREATE INDEX IF NOT EXISTS order_lines_coord_idx  ON public.order_lines (coordination_date);

-- Guard: pick/issue fields and issued lines can only be changed through the functions below.
CREATE OR REPLACE FUNCTION public.order_lines_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp
AS $$
BEGIN
  IF current_setting('app.in_rpc', true) IS DISTINCT FROM '1' THEN
    IF TG_OP = 'INSERT' THEN
      IF NEW.picked_qty <> 0 OR NEW.picked_on IS NOT NULL OR NEW.issued_on IS NOT NULL OR NEW.recipient IS NOT NULL THEN
        RAISE EXCEPTION 'ORDERERR:איסוף וניפוק מתבצעים רק דרך פעולות האיסוף והניפוק.';
      END IF;
    ELSE
      IF (NEW.picked_qty, NEW.picked_on, NEW.issued_on, NEW.recipient)
         IS DISTINCT FROM (OLD.picked_qty, OLD.picked_on, OLD.issued_on, OLD.recipient) THEN
        RAISE EXCEPTION 'ORDERERR:איסוף וניפוק מתבצעים רק דרך פעולות האיסוף והניפוק.';
      END IF;
      IF OLD.issued_on IS NOT NULL
         AND (to_jsonb(NEW) - 'notes' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'notes' - 'updated_at') THEN
        RAISE EXCEPTION 'ORDERERR:שורה שנופקה ניתנת לעריכת הערות בלבד.';
      END IF;
      IF OLD.picked_qty > 0
         AND (NEW.quantity, NEW.length_mm, NEW.supply, NEW.category, NEW.color)
             IS DISTINCT FROM (OLD.quantity, OLD.length_mm, OLD.supply, OLD.category, OLD.color) THEN
        RAISE EXCEPTION 'ORDERERR:שורה שנאספה: יש להחזיר את האיסוף לפני שינוי מידה או כמות.';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS order_lines_guard ON public.order_lines;
CREATE TRIGGER order_lines_guard BEFORE INSERT OR UPDATE ON public.order_lines
  FOR EACH ROW EXECUTE FUNCTION public.order_lines_guard();

-- Access: approved owner only. Delete only for lines with no stock effect.
ALTER TABLE public.order_lines ENABLE ROW LEVEL SECURITY;
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = 'order_lines' LOOP
    EXECUTE format('DROP POLICY %I ON public.order_lines', r.policyname);
  END LOOP;
END $$;
CREATE POLICY order_lines_select ON public.order_lines FOR SELECT TO authenticated
  USING (public.is_approved_user());
CREATE POLICY order_lines_insert ON public.order_lines FOR INSERT TO authenticated
  WITH CHECK (public.is_approved_user());
CREATE POLICY order_lines_update ON public.order_lines FOR UPDATE TO authenticated
  USING (public.is_approved_user()) WITH CHECK (public.is_approved_user());
CREATE POLICY order_lines_delete ON public.order_lines FOR DELETE TO authenticated
  USING (public.is_approved_user() AND picked_qty = 0 AND issued_on IS NULL);

REVOKE ALL ON public.order_lines FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.order_lines TO authenticated;

-- 3. Atomic stock functions ---------------------------------------------------------
-- SECURITY DEFINER (they bypass RLS), so each checks approval itself; EXECUTE is granted
-- only to logged-in users. Each call is one transaction with row locks: a repeated or
-- concurrent call fails without touching stock, and any failure rolls everything back.

-- Pick standard Santaf for an order line: available stock -> staged (deducted NOW).
CREATE OR REPLACE FUNCTION public.pick_stock_line(
  p_line_id uuid, p_qty integer, p_date date, p_staging text
) RETURNS public.order_lines
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $fn$
DECLARE
  l  public.order_lines;
  n  integer;
  d  date := coalesce(p_date, current_date);
  st text := nullif(btrim(coalesce(p_staging, '')), '');
BEGIN
  IF NOT public.is_approved_user() THEN
    RAISE EXCEPTION 'ORDERERR:אין הרשאה לבצע פעולה זו.';
  END IF;
  IF p_qty IS NULL OR p_qty <= 0 THEN
    RAISE EXCEPTION 'ORDERERR:הכמות לאיסוף חייבת להיות גדולה מאפס.';
  END IF;
  IF st IS NULL THEN
    RAISE EXCEPTION 'ORDERERR:חובה להזין מיקום הכנה.';
  END IF;
  SELECT * INTO l FROM public.order_lines WHERE id = p_line_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ORDERERR:שורת ההזמנה לא נמצאה.'; END IF;
  IF l.supply <> 'stock' THEN
    RAISE EXCEPTION 'ORDERERR:רק שורת סנטף מהמלאי הרגיל נאספת מהמלאי.';
  END IF;
  IF l.issued_on IS NOT NULL THEN
    RAISE EXCEPTION 'ORDERERR:השורה כבר נופקה.';
  END IF;
  IF p_qty > l.quantity - l.picked_qty THEN
    RAISE EXCEPTION 'ORDERERR:הכמות גדולה ממה שנותר לאסוף (%).', l.quantity - l.picked_qty;
  END IF;
  SELECT count(*) INTO n FROM public.santaf_inventory WHERE length = l.length_mm;
  IF n <> 1 THEN
    RAISE EXCEPTION 'ORDERERR:לא נמצאה שורת מלאי יחידה לאורך %. לא בוצע שינוי.', l.length_mm;
  END IF;
  UPDATE public.santaf_inventory
     SET quantity = quantity - p_qty
   WHERE length = l.length_mm AND coalesce(quantity, 0) >= p_qty;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN
    RAISE EXCEPTION 'ORDERERR:אין מספיק מלאי זמין באורך % (נדרש %). לא בוצע שינוי.', l.length_mm, p_qty;
  END IF;
  INSERT INTO public.santaf_movements (length, quantity, type, "date")
  VALUES (l.length_mm, p_qty, 'PICK', d);

  PERFORM set_config('app.in_rpc', '1', true);
  UPDATE public.order_lines
     SET picked_qty = picked_qty + p_qty, picked_on = d, staging_location = st, updated_at = now()
   WHERE id = p_line_id RETURNING * INTO l;
  RETURN l;
END
$fn$;

-- Undo a pick (returns the picked quantity to available stock). Not allowed after issuance.
CREATE OR REPLACE FUNCTION public.unpick_stock_line(p_line_id uuid, p_date date)
RETURNS public.order_lines
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $fn$
DECLARE
  l public.order_lines;
  n integer;
  d date := coalesce(p_date, current_date);
BEGIN
  IF NOT public.is_approved_user() THEN
    RAISE EXCEPTION 'ORDERERR:אין הרשאה לבצע פעולה זו.';
  END IF;
  SELECT * INTO l FROM public.order_lines WHERE id = p_line_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ORDERERR:שורת ההזמנה לא נמצאה.'; END IF;
  IF l.supply <> 'stock' THEN
    RAISE EXCEPTION 'ORDERERR:רק שורת סנטף מהמלאי הרגיל נאספת מהמלאי.';
  END IF;
  IF l.issued_on IS NOT NULL THEN
    RAISE EXCEPTION 'ORDERERR:שורה שנופקה אי אפשר להחזיר למלאי.';
  END IF;
  IF l.picked_qty <= 0 THEN
    RAISE EXCEPTION 'ORDERERR:אין איסוף להחזרה.';
  END IF;
  UPDATE public.santaf_inventory
     SET quantity = coalesce(quantity, 0) + l.picked_qty
   WHERE length = l.length_mm;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN
    RAISE EXCEPTION 'ORDERERR:לא נמצאה שורת מלאי יחידה לאורך %. לא בוצע שינוי.', l.length_mm;
  END IF;
  INSERT INTO public.santaf_movements (length, quantity, type, "date")
  VALUES (l.length_mm, l.picked_qty, 'UNPICK', d);

  PERFORM set_config('app.in_rpc', '1', true);
  UPDATE public.order_lines
     SET picked_qty = 0, picked_on = NULL, updated_at = now()
   WHERE id = p_line_id RETURNING * INTO l;
  RETURN l;
END
$fn$;

-- Issue lines to a recipient (one LED package = all its lines at once). All or nothing.
-- Stock lines: the staged quantity leaves the warehouse (movement OUT) WITHOUT a second
-- deduction from available stock. Supplier lines must have been received.
CREATE OR REPLACE FUNCTION public.issue_order_lines(p_ids uuid[], p_date date, p_recipient text)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $fn$
DECLARE
  l   public.order_lines;
  d   date := coalesce(p_date, current_date);
  rcp text := nullif(btrim(coalesce(p_recipient, '')), '');
  found_n integer;
  want_n  integer;
BEGIN
  IF NOT public.is_approved_user() THEN
    RAISE EXCEPTION 'ORDERERR:אין הרשאה לבצע פעולה זו.';
  END IF;
  IF rcp IS NULL THEN RAISE EXCEPTION 'ORDERERR:חובה להזין שם מקבל.'; END IF;
  IF p_ids IS NULL OR cardinality(p_ids) = 0 THEN
    RAISE EXCEPTION 'ORDERERR:לא נבחרו שורות לניפוק.';
  END IF;
  SELECT count(DISTINCT x) INTO want_n FROM unnest(p_ids) AS x;
  SELECT count(*) INTO found_n FROM (
    SELECT id FROM public.order_lines WHERE id = ANY(p_ids) ORDER BY id FOR UPDATE) locked;
  IF found_n <> want_n THEN RAISE EXCEPTION 'ORDERERR:חלק משורות ההזמנה לא נמצאו.'; END IF;

  FOR l IN SELECT * FROM public.order_lines WHERE id = ANY(p_ids) ORDER BY id LOOP
    IF l.issued_on IS NOT NULL THEN
      RAISE EXCEPTION 'ORDERERR:השורה כבר נופקה. לא בוצע שינוי.';
    END IF;
    IF l.supply = 'stock' THEN
      IF l.picked_qty <> l.quantity THEN
        RAISE EXCEPTION 'ORDERERR:שורת סנטף % מ"מ עדיין לא נאספה במלואה.', l.length_mm;
      END IF;
      INSERT INTO public.santaf_movements (length, quantity, type, "date")
      VALUES (l.length_mm, l.picked_qty, 'OUT', d);
    ELSIF l.supplier_status IS DISTINCT FROM 'received' THEN
      RAISE EXCEPTION 'ORDERERR:השורה עדיין לא התקבלה במחסן.';
    END IF;
  END LOOP;

  PERFORM set_config('app.in_rpc', '1', true);
  UPDATE public.order_lines SET issued_on = d, recipient = rcp, updated_at = now()
   WHERE id = ANY(p_ids);
  RETURN want_n;
END
$fn$;

REVOKE ALL ON FUNCTION public.pick_stock_line(uuid, integer, date, text)  FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.unpick_stock_line(uuid, date)               FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.issue_order_lines(uuid[], date, text)       FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.pick_stock_line(uuid, integer, date, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.unpick_stock_line(uuid, date)              TO authenticated;
GRANT EXECUTE ON FUNCTION public.issue_order_lines(uuid[], date, text)      TO authenticated;

COMMIT;
